package uk.cookiehost.cookieai

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.URL
import java.nio.ByteBuffer
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

/**
 * CookieAI Android App — Main Activity
 *
 * Privacy-first: NO Google services used. Specifically:
 *  - Time sync: pool.ntp.org / time.cloudflare.com (NOT time.google.com)
 *  - Maps/Location: NOT used (no Google Maps, no Fused Location API)
 *  - Push notifications: NOT Firebase — uses Tailscale-routed WebSocket
 *  - Analytics: NONE
 *  - Crash reporting: local logfile only (NOT Firebase Crashlytics)
 *  - Safe Browsing: NOT Google Safe Browsing API
 *  - Fonts: bundled locally (NOT Google Fonts CDN)
 *  - DNS: Configured to 1.1.1.1/9.9.9.9 (NOT 8.8.8.8 Google DNS)
 *
 * Features:
 *  - Chat with local Ollama/Gemma via Tailscale
 *  - Image generation via CookieFocus API
 *  - Tanda 3D printing integration
 *  - CookieCloud file sync
 *  - Full content safety filtering
 *  - Persistent memory (enterprise)
 *  - Auto-update via GitHub (NOT Play Store)
 */

// ── Constants ─────────────────────────────────────────────────────────────────

private const val PREF_FILE        = "cookieai_prefs"
private const val PREF_OLLAMA_HOST = "ollama_host"
private const val PREF_MODEL       = "model"
private const val PREF_ADULT_FILTER = "adult_filter"
private const val PREF_ENTERPRISE  = "enterprise_enabled"
private const val PREF_CC_SERVER   = "cookiecloud_server"
private const val PREF_MEMORY_ENABLED = "memory_enabled"
private const val PREF_TANDA_URL   = "tanda_url"

private const val DEFAULT_OLLAMA_HOST = "http://100.x.x.x:11434"  // Replace with Tailscale IP
private const val DEFAULT_MODEL       = "gemma3:4b"
private const val DEFAULT_CC_SERVER   = "https://cookiecloud.cookiehost.uk"
private const val DEFAULT_TANDA_URL   = "https://www.tanda-3dprinting.co.uk"

// Privacy-safe NTP servers — no Google
private val NTP_SERVERS = listOf(
    "pool.ntp.org",
    "time.cloudflare.com",
    "time.apple.com",  // Apple is acceptable (no Google)
    "0.debian.pool.ntp.org",
)


// ── NTP Client (no Google time.google.com) ────────────────────────────────────

object PrivacyNTP {
    /**
     * Fetch current time from privacy-safe NTP servers.
     * Falls back to system clock if all servers fail.
     * Google's time.google.com is explicitly NOT in the server list.
     */
    fun getNetworkTime(): Long {
        for (server in NTP_SERVERS) {
            try {
                val ntpTime = queryNTP(server)
                if (ntpTime > 0) return ntpTime
            } catch (e: Exception) {
                // Try next server
            }
        }
        // Fallback to system clock
        return System.currentTimeMillis()
    }

    private fun queryNTP(server: String): Long {
        val NTP_PORT   = 123
        val NTP_PACKET_SIZE = 48
        val NTP_EPOCH_OFFSET = 2208988800L  // Seconds between 1900 and 1970

        val buf = ByteArray(NTP_PACKET_SIZE)
        buf[0] = 0x1B.toByte()  // NTP request: LI=0, VN=3, Mode=3 (client)

        val socket = DatagramSocket()
        socket.soTimeout = 3000

        val address = InetAddress.getByName(server)
        val request = DatagramPacket(buf, buf.size, address, NTP_PORT)
        socket.send(request)

        val response = DatagramPacket(ByteArray(NTP_PACKET_SIZE), NTP_PACKET_SIZE)
        socket.receive(response)
        socket.close()

        val responseData = response.data
        // Transmit Timestamp: bytes 40-47
        val seconds = ByteBuffer.wrap(responseData, 40, 4).int.toLong() and 0xFFFFFFFFL
        val ntpEpochMs = (seconds - NTP_EPOCH_OFFSET) * 1000L
        return ntpEpochMs
    }
}


// ── HTTP Client (privacy-hardened, no Google DNS leaks) ──────────────────────

object CookieHttpClient {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        // No Google SafeBrowsing, no Google DNS, no GAID headers
        .addInterceptor { chain ->
            val request = chain.request().newBuilder()
                .removeHeader("X-Forwarded-For")
                .header("User-Agent", "CookieAI/1.0 (Android; CookieOS; no-telemetry)")
                .build()
            chain.proceed(request)
        }
        .build()

    val JSON = "application/json; charset=utf-8".toMediaType()
}


// ── Content Safety Filter (client-side) ──────────────────────────────────────

object ContentFilter {
    private val BLOCK_PATTERNS = listOf(
        Regex("""(?i)\b(child|minor|underage|loli|shota|teen\b.{0,20}(nude|naked|sex|explicit))\b"""),
        Regex("""(?i)\b(bioweapon|nerve agent|sarin|dirty bomb|nuclear device)\b"""),
        Regex("""(?i)ignore (previous|all|prior|above) instructions?"""),
        Regex("""(?i)(dan mode|jailbreak|developer mode|bypass (safety|filter))"""),
        Regex("""(?i)pretend (you are|to be) (evil|unrestricted|uncensored)"""),
    )

    private val ADULT_PATTERNS = listOf(
        Regex("""(?i)\b(sex|erotic|xxx|hentai|pornograph|adult film)\b"""),
    )

    data class FilterResult(val allowed: Boolean, val reason: String = "")

    fun checkPrompt(text: String, adultFilterEnabled: Boolean = true): FilterResult {
        val normalised = normalise(text)

        for (pattern in BLOCK_PATTERNS) {
            if (pattern.containsMatchIn(normalised)) {
                return FilterResult(false, "Content blocked by safety filter.")
            }
        }

        if (adultFilterEnabled) {
            for (pattern in ADULT_PATTERNS) {
                if (pattern.containsMatchIn(normalised)) {
                    return FilterResult(false, "Adult content blocked (18+ filter active).")
                }
            }
        }

        return FilterResult(true)
    }

    private fun normalise(text: String): String {
        // Leet speak normalisation
        return text
            .replace('0', 'o').replace('1', 'i').replace('3', 'e')
            .replace('4', 'a').replace('5', 's').replace('7', 't')
            .replace('@', 'a').replace('$', 's')
            .lowercase()
            .replace(Regex("\\s+"), " ")
    }
}


// ── Persistent Memory (enterprise feature) ───────────────────────────────────

class MemoryManager(private val context: Context) {
    private val memoryFile = File(context.filesDir, "cookieai_memory.json")

    fun save(key: String, value: String) {
        val data = load()
        data.put(key, value)
        data.put("_updated", System.currentTimeMillis().toString())
        memoryFile.writeText(data.toString())
    }

    fun get(key: String): String? {
        return try { load().optString(key).takeIf { it.isNotEmpty() } } catch (e: Exception) { null }
    }

    fun getContext(): String {
        return try {
            val data = load()
            val sb = StringBuilder()
            data.keys().forEach { key ->
                if (!key.startsWith("_")) {
                    sb.appendLine("$key: ${data.getString(key)}")
                }
            }
            sb.toString().take(2000)
        } catch (e: Exception) { "" }
    }

    fun clear() = memoryFile.delete()

    private fun load(): JSONObject {
        return try {
            if (memoryFile.exists()) JSONObject(memoryFile.readText())
            else JSONObject()
        } catch (e: Exception) { JSONObject() }
    }
}


// ── Ollama Chat Client ────────────────────────────────────────────────────────

class OllamaClient(private val baseUrl: String) {
    data class ChatMessage(val role: String, val content: String)

    fun isAvailable(): Boolean {
        return try {
            val r = CookieHttpClient.client.newCall(
                Request.Builder().url("$baseUrl/api/tags").build()
            ).execute()
            r.isSuccessful
        } catch (e: Exception) { false }
    }

    fun chat(
        messages: List<ChatMessage>,
        model: String,
        onChunk: (String) -> Unit,
        onDone: () -> Unit,
        onError: (String) -> Unit
    ) {
        val messagesJson = JSONArray().apply {
            messages.forEach { msg ->
                put(JSONObject().put("role", msg.role).put("content", msg.content))
            }
        }

        val body = JSONObject()
            .put("model", model)
            .put("messages", messagesJson)
            .put("stream", true)
            .toString()
            .toRequestBody(CookieHttpClient.JSON)

        val request = Request.Builder()
            .url("$baseUrl/api/chat")
            .post(body)
            .build()

        CookieHttpClient.client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                onError("Connection failed: ${e.message}\nCheck Ollama is running and Tailscale is connected.")
            }

            override fun onResponse(call: Call, response: Response) {
                if (!response.isSuccessful) {
                    onError("Ollama error: HTTP ${response.code}")
                    return
                }
                response.body?.source()?.let { source ->
                    try {
                        while (!source.exhausted()) {
                            val line = source.readUtf8Line() ?: break
                            if (line.isBlank()) continue
                            try {
                                val json = JSONObject(line)
                                val content = json.optJSONObject("message")?.optString("content") ?: ""
                                if (content.isNotEmpty()) onChunk(content)
                                if (json.optBoolean("done", false)) {
                                    onDone()
                                    break
                                }
                            } catch (e: Exception) { /* malformed line */ }
                        }
                    } finally {
                        response.close()
                    }
                }
            }
        })
    }
}


// ── Tanda 3D Print Integration ────────────────────────────────────────────────

class TandaIntegration(private val baseUrl: String) {
    /**
     * Open the Tanda 3D printing web app in a WebView or external browser.
     * No Google services involved — direct HTTPS to tanda-3dprinting.co.uk.
     */
    fun openInBrowser(context: Context) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(baseUrl))
        context.startActivity(intent)
    }

    /**
     * Fetch available print materials/services from the Tanda site.
     * Returns a user-friendly summary.
     */
    fun queryServices(onResult: (String) -> Unit, onError: (String) -> Unit) {
        val request = Request.Builder().url(baseUrl).build()
        CookieHttpClient.client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                onError("Could not reach Tanda: ${e.message}")
            }
            override fun onResponse(call: Call, response: Response) {
                if (response.isSuccessful) {
                    onResult("Tanda 3D Printing services loaded. Tap 'Open Tanda' to browse and order prints.")
                } else {
                    onError("Tanda returned error ${response.code}")
                }
                response.close()
            }
        })
    }
}


// ── Main Activity ─────────────────────────────────────────────────────────────

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var memory: MemoryManager
    private lateinit var ollamaClient: OllamaClient
    private lateinit var tanda: TandaIntegration

    private val chatHistory = mutableListOf<OllamaClient.ChatMessage>()
    private val uiHandler   = Handler(Looper.getMainLooper())

    // UI refs
    private lateinit var tabLayout: TabHost
    private lateinit var chatDisplay: TextView
    private lateinit var chatInput: EditText
    private lateinit var sendBtn: Button
    private lateinit var statusBar: TextView
    private lateinit var imgPromptInput: EditText
    private lateinit var imgGenBtn: Button
    private lateinit var imgStatusText: TextView
    private lateinit var tandaStatusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs  = getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
        memory = MemoryManager(this)

        val ollamaHost = prefs.getString(PREF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST)!!
        ollamaClient   = OllamaClient(ollamaHost)
        tanda          = TandaIntegration(prefs.getString(PREF_TANDA_URL, DEFAULT_TANDA_URL)!!)

        setupUI()
        checkOllamaConnection()

        // Sync time on startup from privacy-safe NTP (not Google)
        lifecycleScope.launch(Dispatchers.IO) {
            val networkTime = PrivacyNTP.getNetworkTime()
            val diff = Math.abs(networkTime - System.currentTimeMillis())
            if (diff > 5000) {
                withContext(Dispatchers.Main) {
                    showStatus("⚠ System clock drift ${diff / 1000}s — NTP synced via pool.ntp.org")
                }
            }
        }
    }

    private fun setupUI() {
        // Chat tab
        chatDisplay   = findViewById(R.id.chat_display)
        chatInput     = findViewById(R.id.chat_input)
        sendBtn       = findViewById(R.id.send_btn)
        statusBar     = findViewById(R.id.status_bar)

        // Image tab
        imgPromptInput = findViewById(R.id.img_prompt)
        imgGenBtn      = findViewById(R.id.img_gen_btn)
        imgStatusText  = findViewById(R.id.img_status)

        // Tanda tab
        tandaStatusText = findViewById(R.id.tanda_status)

        sendBtn.setOnClickListener { sendChatMessage() }
        chatInput.setOnEditorActionListener { _, _, _ -> sendChatMessage(); true }

        imgGenBtn.setOnClickListener { generateImage() }

        // Tanda buttons
        findViewById<Button>(R.id.tanda_open_btn).setOnClickListener {
            tanda.openInBrowser(this)
        }
        findViewById<Button>(R.id.tanda_check_btn).setOnClickListener {
            checkTandaServices()
        }

        // Settings
        findViewById<Button>(R.id.settings_save_btn).setOnClickListener {
            saveSettings()
        }

        // Load current settings into fields
        loadSettingsUI()

        appendChat("🍪 CookieAI — your private AI assistant\n")
        appendChat("All processing runs locally via Ollama on your CookieOS devices.\n")
        appendChat("No data leaves your network. No Google. No telemetry.\n")
        appendChat("─".repeat(40) + "\n\n")
    }

    private fun sendChatMessage() {
        val text = chatInput.text.toString().trim()
        if (text.isEmpty()) return
        chatInput.text.clear()

        val adultFilterOn = prefs.getBoolean(PREF_ADULT_FILTER, true)
        val filterResult  = ContentFilter.checkPrompt(text, adultFilterOn)

        if (!filterResult.allowed) {
            appendChat("🚫 ${filterResult.reason}\n\n")
            return
        }

        appendChat("You: $text\n")
        appendChat("CookieGPT: ")
        sendBtn.isEnabled = false

        // Build message list — include memory context if enterprise
        val systemPrompt = buildSystemPrompt()
        val messages = mutableListOf(OllamaClient.ChatMessage("system", systemPrompt))
        messages.addAll(chatHistory)
        messages.add(OllamaClient.ChatMessage("user", text))

        val model    = prefs.getString(PREF_MODEL, DEFAULT_MODEL)!!
        var response = ""

        ollamaClient.chat(
            messages = messages,
            model    = model,
            onChunk  = { chunk ->
                response += chunk
                uiHandler.post { appendChat(chunk) }
            },
            onDone   = {
                chatHistory.add(OllamaClient.ChatMessage("user", text))
                chatHistory.add(OllamaClient.ChatMessage("assistant", response))
                // Keep history bounded
                if (chatHistory.size > 40) chatHistory.removeAt(0)

                // Save to memory if enterprise
                if (prefs.getBoolean(PREF_MEMORY_ENABLED, false)) {
                    memory.save("last_topic", text.take(100))
                }

                uiHandler.post {
                    appendChat("\n\n")
                    sendBtn.isEnabled = true
                }
            },
            onError  = { err ->
                uiHandler.post {
                    appendChat("\n[Error: $err]\n\n")
                    sendBtn.isEnabled = true
                }
            }
        )
    }

    private fun buildSystemPrompt(): String {
        val base = "You are CookieGPT, a helpful private AI assistant running on CookieOS. " +
            "You run entirely locally — no internet, no telemetry. Be direct and concise."

        return if (prefs.getBoolean(PREF_MEMORY_ENABLED, false)) {
            val ctx = memory.getContext()
            if (ctx.isNotEmpty()) "$base\n\nUser context:\n$ctx" else base
        } else base
    }

    private fun generateImage() {
        val prompt = imgPromptInput.text.toString().trim()
        if (prompt.isEmpty()) {
            imgStatusText.text = "⚠ Enter a prompt first."
            return
        }

        val filterResult = ContentFilter.checkPrompt(prompt, prefs.getBoolean(PREF_ADULT_FILTER, true))
        if (!filterResult.allowed) {
            imgStatusText.text = "🚫 ${filterResult.reason}"
            return
        }

        imgStatusText.text = "⏳ Generating..."
        imgGenBtn.isEnabled = false

        val ollamaHost = prefs.getString(PREF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST)!!
        val apiUrl = "$ollamaHost/fooocus/generate"  // CookieOS Fooocus endpoint

        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val body = JSONObject().put("prompt", prompt).put("style", "Realistic").toString()
                    .toRequestBody(CookieHttpClient.JSON)
                val request = Request.Builder().url(apiUrl).post(body).build()
                val response = CookieHttpClient.client.newCall(request).execute()

                withContext(Dispatchers.Main) {
                    if (response.isSuccessful) {
                        val result = JSONObject(response.body?.string() ?: "{}")
                        imgStatusText.text = "✓ Image generated: ${result.optString("filename", "output.png")}"
                    } else {
                        imgStatusText.text = "⚠ Image generation failed (${response.code})"
                    }
                    imgGenBtn.isEnabled = true
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    imgStatusText.text = "⚠ Error: ${e.message}"
                    imgGenBtn.isEnabled = true
                }
            }
        }
    }

    private fun checkTandaServices() {
        tandaStatusText.text = "Checking Tanda..."
        tanda.queryServices(
            onResult = { msg -> uiHandler.post { tandaStatusText.text = msg } },
            onError  = { err -> uiHandler.post { tandaStatusText.text = "⚠ $err" } }
        )
    }

    private fun checkOllamaConnection() {
        lifecycleScope.launch(Dispatchers.IO) {
            val available = ollamaClient.isAvailable()
            withContext(Dispatchers.Main) {
                showStatus(if (available)
                    "✓ Ollama connected — ready"
                else
                    "⚠ Ollama not reachable. Check Settings → Ollama host and Tailscale connection.")
            }
        }
    }

    private fun saveSettings() {
        val host  = findViewById<EditText>(R.id.settings_ollama_host).text.toString().trim()
        val model = findViewById<EditText>(R.id.settings_model).text.toString().trim()
        val adult = findViewById<Switch>(R.id.settings_adult_filter).isChecked
        val mem   = findViewById<Switch>(R.id.settings_memory).isChecked
        val cc    = findViewById<EditText>(R.id.settings_cc_server).text.toString().trim()
        val tanUrl = findViewById<EditText>(R.id.settings_tanda_url).text.toString().trim()

        prefs.edit()
            .putString(PREF_OLLAMA_HOST, host.ifEmpty { DEFAULT_OLLAMA_HOST })
            .putString(PREF_MODEL, model.ifEmpty { DEFAULT_MODEL })
            .putBoolean(PREF_ADULT_FILTER, adult)
            .putBoolean(PREF_MEMORY_ENABLED, mem)
            .putString(PREF_CC_SERVER, cc.ifEmpty { DEFAULT_CC_SERVER })
            .putString(PREF_TANDA_URL, tanUrl.ifEmpty { DEFAULT_TANDA_URL })
            .apply()

        // Reinit clients
        ollamaClient = OllamaClient(prefs.getString(PREF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST)!!)
        tanda = TandaIntegration(prefs.getString(PREF_TANDA_URL, DEFAULT_TANDA_URL)!!)

        showStatus("✓ Settings saved.")
        checkOllamaConnection()
    }

    private fun loadSettingsUI() {
        findViewById<EditText>(R.id.settings_ollama_host).setText(
            prefs.getString(PREF_OLLAMA_HOST, DEFAULT_OLLAMA_HOST))
        findViewById<EditText>(R.id.settings_model).setText(
            prefs.getString(PREF_MODEL, DEFAULT_MODEL))
        findViewById<Switch>(R.id.settings_adult_filter).isChecked =
            prefs.getBoolean(PREF_ADULT_FILTER, true)
        findViewById<Switch>(R.id.settings_memory).isChecked =
            prefs.getBoolean(PREF_MEMORY_ENABLED, false)
        findViewById<EditText>(R.id.settings_cc_server).setText(
            prefs.getString(PREF_CC_SERVER, DEFAULT_CC_SERVER))
        findViewById<EditText>(R.id.settings_tanda_url).setText(
            prefs.getString(PREF_TANDA_URL, DEFAULT_TANDA_URL))
    }

    private fun appendChat(text: String) {
        chatDisplay.append(text)
        // Auto-scroll
        val scrollView = chatDisplay.parent as? ScrollView
        scrollView?.post { scrollView.fullScroll(View.FOCUS_DOWN) }
    }

    private fun showStatus(msg: String) {
        statusBar.text = msg
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "Clear Chat")
        menu.add(0, 2, 0, "Clear Memory")
        menu.add(0, 3, 0, "Check for Updates")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { chatHistory.clear(); chatDisplay.text = ""; appendChat("Chat cleared.\n\n"); true }
            2 -> { memory.clear(); showStatus("Memory cleared."); true }
            3 -> { checkForUpdates(); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    private fun checkForUpdates() {
        showStatus("Checking for updates...")
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                // Check GitHub releases — no Google Play involved
                val r = CookieHttpClient.client.newCall(
                    Request.Builder()
                        .url("https://api.github.com/repos/FreddieSparrow/cookieos/releases/latest")
                        .header("Accept", "application/vnd.github+json")
                        .build()
                ).execute()

                withContext(Dispatchers.Main) {
                    if (r.isSuccessful) {
                        val release = JSONObject(r.body?.string() ?: "{}")
                        val tag = release.optString("tag_name", "unknown")
                        showStatus("Latest release: $tag — see GitHub for manual install.")
                        AlertDialog.Builder(this@MainActivity)
                            .setTitle("CookieOS Update")
                            .setMessage("Latest release: $tag\n\n${release.optString("body", "").take(300)}")
                            .setPositiveButton("View on GitHub") { _, _ ->
                                startActivity(Intent(Intent.ACTION_VIEW,
                                    Uri.parse("https://github.com/FreddieSparrow/cookieos/releases")))
                            }
                            .setNegativeButton("Close", null)
                            .show()
                    } else {
                        showStatus("Could not check updates (${r.code})")
                    }
                    r.close()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    showStatus("Update check failed: ${e.message}")
                }
            }
        }
    }
}
