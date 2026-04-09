// CookieOS Threat Detector for Android (Kotlin)
// Runs on AOSP with Termux, communicates with Ollama via Tailscale
// Detects malware, generates patches, manages app/binary security

package uk.cookiehost.cookieos.threatdetection

import android.content.Context
import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.util.Log
import kotlinx.coroutines.*
import java.io.File
import java.io.BufferedInputStream
import java.security.MessageDigest
import kotlin.math.log2
import okhttp3.*
import com.google.gson.Gson
import com.google.gson.JsonObject
import java.time.Instant

/**
 * CookieOS AI Threat Detector for Android
 *
 * Features:
 * - Real-time binary/APK analysis via Ollama (local + Tailscale)
 * - AppArmor-style permission restrictions
 * - Fire app patches via SELinux policy
 * - Quarantine suspicious APKs before installation
 * - Hash-based caching for offline performance
 * - Integration with CookieCloud threat intelligence
 */

class AnyViewModel {
    companion object {
        const val TAG = "CookieThreatDetector"
        const val OLLAMA_TIMEOUT_SECONDS = 30L
        const val TAILSCALE_GATEWAY = "100.100.100.100:11434"  // Tailscale loopback
        const val MAX_APK_SCAN_SIZE = 512 * 1024 * 1024  // 512 MB
    }
}

enum class ThreatLevel {
    SAFE, SUSPICIOUS, HIGH, CRITICAL
}

enum class ThreatType {
    APK, BINARY, Permission, BEHAVIORAL
}

data class ThreatDetection(
    val detectionId: String,
    val threatType: ThreatType,
    val packageName: String?,
    val filePath: String?,
    val severity: ThreatLevel,
    val evidence: Map<String, Any>,
    val aiAnalysis: String,
    val timestamp: Long = System.currentTimeMillis(),
    val patchApplied: Boolean = false,
    val trustScore: Float = 0.5f
)

data class ThreatSignature(
    val threatId: String,
    val patternType: String,  // "permissions", "syscalls", "urls"
    val pattern: String,
    val description: String,
    val severity: ThreatLevel,
    val aiConfidence: Float
)

class BinaryAnalyzer {
    private val log = Log.getLogger(TAG)

    fun calculateEntropy(data: ByteArray): Double {
        if (data.isEmpty() || data.size < 256) return 0.0

        val frequencies = IntArray(256)
        for (byte in data) {
            frequencies[byte.toInt() and 0xFF]++
        }

        var entropy = 0.0
        for (count in frequencies) {
            if (count > 0) {
                val p = count.toDouble() / data.size
                entropy -= p * log2(p)
            }
        }
        return entropy
    }

    fun detectPackers(header: ByteArray): List<String> {
        val packers = mutableListOf<String>()
        val signatures = mapOf(
            "UPX!" to "UPX packer",
            "\u005d\u0000\u0000\u0000" to "LZMA compressed",
            "META-INF" to "APK archive"
        )

        val headerStr = String(header, Charsets.ISO_8859_1)
        for ((sig, name) in signatures) {
            if (headerStr.contains(sig)) {
                packers.add(name)
            }
        }
        return packers
    }

    fun analyzeAPKSignature(apkPath: String): Map<String, Any> {
        // Use aapt2 to extract APK certificate info
        return try {
            val process = Runtime.getRuntime().exec(arrayOf("aapt2", "dump", "badging", apkPath))
            val output = process.inputStream.bufferedReader().readText()

            mapOf(
                "has_signature" to output.contains("sha256-fingerprint"),
                "signature_valid" to !output.contains("ERROR"),
                "raw_output" to output.take(500)
            )
        } catch (e: Exception) {
            mapOf("error" to e.message)
        }
    }
}

class PermissionAnalyzer {
    private val dangerousPermissions = setOf(
        "android.permission.INSTALL_PACKAGES",
        "android.permission.WRITE_SECURE_SETTINGS",
        "android.permission.MODIFY_PHONE_STATE",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.RECORD_AUDIO",
        "android.permission.CAMERA",
        "android.permission.READ_CONTACTS",
        "android.permission.READ_SMS",
        "android.permission.SEND_SMS"
    )

    fun extractAPKPermissions(apkPath: String): List<String> {
        return try {
            val process = Runtime.getRuntime().exec(arrayOf("aapt2", "dump", "permissions", apkPath))
            val output = process.inputStream.bufferedReader().readText()
            output.split("\n")
                .filter { it.trim().startsWith("permission:") }
                .map { it.replace("permission:", "").trim() }
        } catch (e: Exception) {
            emptyList()
        }
    }

    fun flagSuspiciousPermissions(apkPath: String): Map<String, Any> {
        val permissions = extractAPKPermissions(apkPath)
        val suspicious = permissions.filter { it in dangerousPermissions }
        val riskScore = (suspicious.size.toFloat() / dangerousPermissions.size) * 100

        return mapOf(
            "total_dangerous" to suspicious.size,
            "permissions" to suspicious,
            "risk_percentage" to riskScore
        )
    }
}

class AIThreatAnalyzer(private val context: Context) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(AnyViewModel.OLLAMA_TIMEOUT_SECONDS, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(AnyViewModel.OLLAMA_TIMEOUT_SECONDS, java.util.concurrent.TimeUnit.SECONDS)
        .build()
    private val gson = Gson()
    private val log = Log.getLogger(AnyViewModel.TAG)

    suspend fun analyzeAPK(apkPath: String, header: ByteArray): Pair<ThreatLevel, String> {
        return withContext(Dispatchers.IO) {
            try {
                val binaryAnalyzer = BinaryAnalyzer()
                val permAnalyzer = PermissionAnalyzer()

                val entropy = binaryAnalyzer.calculateEntropy(header)
                val packers = binaryAnalyzer.detectPackers(header)
                val permissions = permAnalyzer.flagSuspiciousPermissions(apkPath)
                val signature = binaryAnalyzer.analyzeAPKSignature(apkPath)

                // Build AI prompt
                val prompt = """
                Analyze this Android APK for malware:
                File: ${File(apkPath).name}
                Entropy: ${"%.2f".format(entropy)}
                Packers: ${packers.joinToString(", ")}
                Dangerous permissions: ${permissions["total_dangerous"]} (${permissions["risk_percentage"]}% of known dangerous permissions)
                Signing certificate: ${signature.getOrDefault("has_signature", "unknown")}

                Respond with only:
                THREAT_LEVEL: [SAFE|SUSPICIOUS|HIGH|CRITICAL]
                CONFIDENCE: [0.0-1.0]
                REASON: [brief]
                """.trimIndent()

                val requestBody = FormBody.Builder()
                    .add("model", "gemma3:2b")  // Phone uses Gemma 3 2B
                    .add("prompt", prompt)
                    .add("stream", "false")
                    .build()

                val request = Request.Builder()
                    .url("http://${AnyViewModel.TAILSCALE_GATEWAY}/api/generate")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val responseBody = response.body?.string() ?: ""

                if (response.code == 200) {
                    val json = gson.fromJson(responseBody, JsonObject::class.java)
                    val analysis = json.get("response")?.asString ?: ""

                    // Parse threat level
                    val threatLevel = when {
                        analysis.contains("CRITICAL", ignoreCase = true) -> ThreatLevel.CRITICAL
                        analysis.contains("HIGH", ignoreCase = true) -> ThreatLevel.HIGH
                        analysis.contains("SUSPICIOUS", ignoreCase = true) -> ThreatLevel.SUSPICIOUS
                        analysis.contains("SAFE", ignoreCase = true) -> ThreatLevel.SAFE
                        else -> ThreatLevel.SUSPICIOUS
                    }

                    Pair(threatLevel, analysis)
                } else {
                    log.w(AnyViewModel.TAG, "Ollama API error: ${response.code}")
                    Pair(ThreatLevel.SUSPICIOUS, "API error")
                }

            } catch (e: Exception) {
                log.e(AnyViewModel.TAG, "Error analyzing APK: ${e.message}")
                Pair(ThreatLevel.SUSPICIOUS, e.message ?: "Unknown error")
            }
        }
    }

    suspend fun generatePatch(detection: ThreatDetection): String? {
        return withContext(Dispatchers.IO) {
            try {
                val prompt = """
                Generate a SELinux policy patch to contain this APK threat:

                Package: ${detection.packageName}
                Severity: ${detection.severity}
                Evidence: ${detection.evidence}

                Return ONLY a valid SELinux policy (policy.conf syntax), no explanations.
                Use 'deny' rules to restrict access to:
                - sensitive system directories (/system, /data/system)
                - network raw sockets
                - device access
                """.trimIndent()

                val requestBody = FormBody.Builder()
                    .add("model", "gemma3:2b")
                    .add("prompt", prompt)
                    .add("stream", "false")
                    .build()

                val request = Request.Builder()
                    .url("http://${AnyViewModel.TAILSCALE_GATEWAY}/api/generate")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val responseBody = response.body?.string() ?: ""

                if (response.code == 200) {
                    val json = gson.fromJson(responseBody, JsonObject::class.java)
                    return@withContext json.get("response")?.asString
                }
                return@withContext null

            } catch (e: Exception) {
                log.e(AnyViewModel.TAG, "Error generating patch: ${e.message}")
                return@withContext null
            }
        }
    }
}

class ThreatDetectionEngine(private val context: Context) {
    private val binaryAnalyzer = BinaryAnalyzer()
    private val permissionAnalyzer = PermissionAnalyzer()
    private val aiAnalyzer = AIThreatAnalyzer(context)
    private val log = Log.getLogger(AnyViewModel.TAG)
    private val scope = CoroutineScope(Dispatchers.Default + Job())
    private val hashCache = mutableMapOf<String, CachedResult>()

    data class CachedResult(
        val threatLevel: ThreatLevel,
        val confidence: Float,
        val timestamp: Long
    )

    fun calculateFileSHA256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { fis ->
            val buffer = ByteArray(8192)
            var bytesRead: Int
            while (fis.read(buffer).also { bytesRead = it } != -1) {
                digest.update(buffer, 0, bytesRead)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    fun calculateTrustScore(entropy: Double, permRisk: Float, aiConfidence: Float): Float {
        // Weighted combination of signals
        val entropyScore = (entropy / 8.0).coerceIn(0.0, 1.0)
        return (
            entropyScore * 0.3f +
            (permRisk / 100f) * 0.3f +
            aiConfidence * 0.4f
            ).toFloat()
    }

    suspend fun scanAPK(apkPath: String): ThreatDetection? {
        return withContext(Dispatchers.IO) {
            try {
                val apkFile = File(apkPath)
                if (!apkFile.exists() || apkFile.length() > AnyViewModel.MAX_APK_SCAN_SIZE) {
                    return@withContext null
                }

                // Check cache
                val sha256 = calculateFileSHA256(apkFile)
                val cached = hashCache[sha256]
                if (cached != null && System.currentTimeMillis() - cached.timestamp < 30 * 24 * 60 * 60 * 1000) {
                    if (cached.threatLevel != ThreatLevel.SAFE) {
                        return@withContext ThreatDetection(
                            detectionId = sha256.take(16),
                            threatType = ThreatType.APK,
                            packageName = null,
                            filePath = apkPath,
                            severity = cached.threatLevel,
                            evidence = mapOf("cached" to true),
                            aiAnalysis = "(cached result)",
                            trustScore = cached.confidence
                        )
                    }
                    return@withContext null
                }

                // Read header
                val header = ByteArray(4096)
                apkFile.inputStream().use {
                    it.read(header)
                }

                // Analyze
                val (threatLevel, analysis) = aiAnalyzer.analyzeAPK(apkPath, header)

                if (threatLevel != ThreatLevel.SAFE) {
                    val entropy = binaryAnalyzer.calculateEntropy(header)
                    val permRisk = (permissionAnalyzer.flagSuspiciousPermissions(apkPath)["risk_percentage"] as? Number)?.toFloat() ?: 0f
                    val trustScore = calculateTrustScore(entropy, permRisk, if (threatLevel == ThreatLevel.CRITICAL) 0.9f else 0.6f)

                    val detection = ThreatDetection(
                        detectionId = sha256.take(16),
                        threatType = ThreatType.APK,
                        packageName = null,
                        filePath = apkPath,
                        severity = threatLevel,
                        evidence = mapOf(
                            "entropy" to "%.2f".format(entropy),
                            "permissions" to permissionAnalyzer.flagSuspiciousPermissions(apkPath)
                        ),
                        aiAnalysis = analysis,
                        trustScore = trustScore
                    )

                    // Cache result
                    hashCache[sha256] = CachedResult(threatLevel, trustScore, System.currentTimeMillis())

                    return@withContext detection
                }

                return@withContext null

            } catch (e: Exception) {
                log.e(AnyViewModel.TAG, "Error scanning APK: ${e.message}")
                return@withContext null
            }
        }
    }

    suspend fun quarantineAPK(apkPath: String): Boolean {
        return withContext(Dispatchers.IO) {
            try {
                val quarantineDir = File(context.cacheDir, "quarantine")
                quarantineDir.mkdirs()

                val apkFile = File(apkPath)
                val quarantinedFile = File(quarantineDir, apkFile.name + ".quarantined")

                apkFile.copyTo(quarantinedFile, overwrite = true)
                apkFile.delete()

                log.i(AnyViewModel.TAG, "APK quarantined: $quarantinedFile")
                return@withContext true

            } catch (e: Exception) {
                log.e(AnyViewModel.TAG, "Error quarantining APK: ${e.message}")
                return@withContext false
            }
        }
    }
}

class ThreatDetectorService : android.app.job.JobService() {
    private val scope = CoroutineScope(Dispatchers.Default + Job())

    override fun onStartJob(params: JobParameters?): Boolean {
        scope.launch {
            val engine = ThreatDetectionEngine(applicationContext)

            // Scan /data/app for suspicious APKs
            val appDir = File("/data/app")
            if (appDir.exists()) {
                appDir.walk().filter { it.name.endsWith(".apk") }.forEach { apkFile ->
                    val detection = engine.scanAPK(apkFile.absolutePath)
                    if (detection != null && detection.severity in listOf(ThreatLevel.HIGH, ThreatLevel.CRITICAL)) {
                        Log.i(AnyViewModel.TAG, "Threat detected in ${apkFile.name}: ${detection.severity}")
                        engine.quarantineAPK(apkFile.absolutePath)
                    }
                }
            }

            jobFinished(params, false)  // Don't reschedule
        }

        return true
    }

    override fun onStopJob(params: JobParameters?): Boolean {
        return false
    }
}

// Usage: Schedule periodic threat scans
fun scheduleThreatDetectionJob(context: Context) {
    val jobScheduler = context.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler
    val jobInfo = JobInfo.Builder(1, ComponentName(context, ThreatDetectorService::class.java))
        .setRequiresDeviceIdle(true)
        .setRequiredNetworkType(JobInfo.NETWORK_TYPE_NONE)  // Works offline
        .setPeriodic(24 * 60 * 60 * 1000)  // Daily
        .build()

    jobScheduler.schedule(jobInfo)
}
