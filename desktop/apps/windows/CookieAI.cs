using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;

/*
 * CookieAI — Windows Desktop App (.NET 8 / WPF)
 *
 * Privacy: Zero telemetry. No Microsoft account required.
 *   - No Microsoft Application Insights
 *   - No Windows Error Reporting (WER) submissions from this app
 *   - No MSIX/Store telemetry — distributed as portable EXE
 *   - Time: uses Windows NTP (configurable), NOT SNTP via Microsoft servers
 *   - DNS: honours system DNS (users should configure to 1.1.1.1/9.9.9.9)
 *
 * Open-source edition:   basic chat + image generation
 * Enterprise edition:    + persistent memory, fleet management, auto-update
 *
 * Build:
 *   dotnet publish -c Release -r win-x64 --self-contained
 *
 * Requires:
 *   - .NET 8 SDK
 *   - Windows 10 1903+ (WPF)
 *   - Ollama running locally or over Tailscale
 */

namespace CookieAI
{
    // ── App entry point ───────────────────────────────────────────────────────

    public partial class App : Application
    {
        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);
            var mainWindow = new MainWindow();
            mainWindow.Show();
        }
    }

    // ── Settings ──────────────────────────────────────────────────────────────

    public class AppSettings
    {
        private static readonly string SettingsPath =
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                         "CookieAI", "settings.json");

        public string OllamaHost   { get; set; } = "http://localhost:11434";
        public string Model        { get; set; } = "gemma3:4b";
        public bool   AdultFilter  { get; set; } = true;   // 18+ filter — on by default
        public bool   MemoryEnabled { get; set; } = false; // Enterprise only
        public string CookieCloudServer { get; set; } = "https://cookiecloud.cookiehost.uk";
        public bool   AutoUpdate   { get; set; } = true;
        public bool   EnterpriseEnabled { get; set; } = false;

        public static AppSettings Load()
        {
            try
            {
                if (File.Exists(SettingsPath))
                    return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(SettingsPath))
                           ?? new AppSettings();
            }
            catch { }
            return new AppSettings();
        }

        public void Save()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
            File.WriteAllText(SettingsPath,
                JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
    }

    // ── Content Safety Filter ─────────────────────────────────────────────────

    public static class ContentFilter
    {
        private static readonly string[] BlockPatterns =
        {
            @"(?i)\b(child|minor|underage|loli|shota|teen\b.{0,20}(nude|naked|sex|explicit))\b",
            @"(?i)\b(bioweapon|nerve agent|sarin|dirty bomb|nuclear device)\b",
            @"(?i)ignore (previous|all|prior|above) instructions?",
            @"(?i)(dan mode|jailbreak|developer mode|bypass (safety|filter))",
            @"(?i)pretend (you are|to be) (evil|unrestricted|uncensored)",
        };

        private static readonly string[] AdultPatterns =
        {
            @"(?i)\b(sex|erotic|xxx|hentai|pornograph|adult film)\b",
        };

        public record FilterResult(bool Allowed, string Reason = "");

        public static FilterResult Check(string text, bool adultFilterEnabled = true)
        {
            string normalised = Normalise(text);

            foreach (var pattern in BlockPatterns)
            {
                if (System.Text.RegularExpressions.Regex.IsMatch(normalised, pattern))
                    return new FilterResult(false, "Content blocked by safety filter.");
            }

            if (adultFilterEnabled)
            {
                foreach (var pattern in AdultPatterns)
                {
                    if (System.Text.RegularExpressions.Regex.IsMatch(normalised, pattern))
                        return new FilterResult(false, "Adult content blocked (18+ filter active). Disable in Settings.");
                }
            }

            return new FilterResult(true);
        }

        private static string Normalise(string text)
        {
            return text
                .Replace('0', 'o').Replace('1', 'i').Replace('3', 'e')
                .Replace('4', 'a').Replace('5', 's').Replace('7', 't')
                .Replace('@', 'a').Replace('$', 's')
                .ToLower()
                .Trim();
        }
    }

    // ── Persistent Memory (enterprise) ───────────────────────────────────────

    public class MemoryManager
    {
        private static readonly string MemoryPath =
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                         "CookieAI", "memory.json");

        private Dictionary<string, string> _data = new();

        public MemoryManager() => Load();

        public void Set(string key, string value)
        {
            _data[key] = value;
            _data["_updated"] = DateTimeOffset.UtcNow.ToString("o");
            Save();
        }

        public string? Get(string key) => _data.TryGetValue(key, out var v) ? v : null;

        public string GetContext()
        {
            var sb = new StringBuilder();
            foreach (var kv in _data)
                if (!kv.Key.StartsWith("_"))
                    sb.AppendLine($"{kv.Key}: {kv.Value}");
            return sb.ToString()[..Math.Min(sb.Length, 2000)];
        }

        public void Clear() { _data.Clear(); Save(); }

        private void Load()
        {
            try
            {
                if (File.Exists(MemoryPath))
                    _data = JsonSerializer.Deserialize<Dictionary<string, string>>(
                        File.ReadAllText(MemoryPath)) ?? new();
            }
            catch { _data = new(); }
        }

        private void Save()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(MemoryPath)!);
            File.WriteAllText(MemoryPath,
                JsonSerializer.Serialize(_data, new JsonSerializerOptions { WriteIndented = true }));
        }
    }

    // ── Ollama Chat Client ────────────────────────────────────────────────────

    public class OllamaClient
    {
        private readonly HttpClient _http;
        private readonly string _baseUrl;

        public OllamaClient(string baseUrl)
        {
            _baseUrl = baseUrl.TrimEnd('/');
            _http = new HttpClient
            {
                Timeout = TimeSpan.FromMinutes(3),
            };
            // No Google/Microsoft telemetry headers
            _http.DefaultRequestHeaders.Add("User-Agent", "CookieAI/1.0 (Windows; CookieOS; no-telemetry)");
        }

        public async Task<bool> IsAvailableAsync()
        {
            try
            {
                var r = await _http.GetAsync($"{_baseUrl}/api/tags");
                return r.IsSuccessStatusCode;
            }
            catch { return false; }
        }

        public record ChatMessage(string Role, string Content);

        /// <summary>
        /// Stream chat response from Ollama. Calls onChunk for each token.
        /// </summary>
        public async Task ChatStreamAsync(
            IEnumerable<ChatMessage> messages,
            string model,
            Action<string> onChunk,
            CancellationToken ct = default)
        {
            var payload = new
            {
                model,
                messages = messages.Select(m => new { role = m.Role, content = m.Content }),
                stream = true,
            };

            var json    = JsonSerializer.Serialize(payload);
            var content = new StringContent(json, Encoding.UTF8, "application/json");
            var request = new HttpRequestMessage(HttpMethod.Post, $"{_baseUrl}/api/chat")
            {
                Content = content,
            };

            using var response = await _http.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, ct);

            response.EnsureSuccessStatusCode();

            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var reader = new StreamReader(stream);

            while (!reader.EndOfStream)
            {
                ct.ThrowIfCancellationRequested();
                var line = await reader.ReadLineAsync(ct);
                if (string.IsNullOrWhiteSpace(line)) continue;

                try
                {
                    using var doc = JsonDocument.Parse(line);
                    var root = doc.RootElement;
                    if (root.TryGetProperty("message", out var msg) &&
                        msg.TryGetProperty("content", out var chunkEl))
                    {
                        var chunk = chunkEl.GetString();
                        if (!string.IsNullOrEmpty(chunk)) onChunk(chunk);
                    }
                    if (root.TryGetProperty("done", out var done) && done.GetBoolean())
                        break;
                }
                catch (JsonException) { /* malformed line */ }
            }
        }
    }

    // ── Auto-Updater (GitHub, no Microsoft Store) ─────────────────────────────

    public class AutoUpdater
    {
        private static readonly string GithubApiUrl =
            "https://api.github.com/repos/FreddieSparrow/cookieos/releases/latest";

        private readonly HttpClient _http = new();

        public record UpdateInfo(string CurrentVersion, string LatestVersion, string ReleaseNotes, string HtmlUrl);

        public async Task<UpdateInfo?> CheckAsync(string currentVersion)
        {
            try
            {
                _http.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "CookieAI-Updater/1.0");
                _http.DefaultRequestHeaders.TryAddWithoutValidation("Accept", "application/vnd.github+json");

                var r = await _http.GetAsync(GithubApiUrl);
                if (!r.IsSuccessStatusCode) return null;

                using var doc  = JsonDocument.Parse(await r.Content.ReadAsStringAsync());
                var root       = doc.RootElement;
                var latest     = root.GetProperty("tag_name").GetString()?.TrimStart('v') ?? "0";
                var notes      = root.TryGetProperty("body", out var b) ? b.GetString() ?? "" : "";
                var htmlUrl    = root.TryGetProperty("html_url", out var u) ? u.GetString() ?? "" : "";

                // Simple version comparison
                if (string.Compare(latest, currentVersion.TrimStart('v'),
                    StringComparison.OrdinalIgnoreCase) > 0)
                {
                    return new UpdateInfo(currentVersion, latest,
                        notes.Length > 500 ? notes[..500] : notes, htmlUrl);
                }
            }
            catch { }
            return null;
        }
    }

    // ── Main Window (WPF) ─────────────────────────────────────────────────────

    public partial class MainWindow : Window
    {
        private const string AppVersion = "1.0.0";

        private AppSettings _settings = AppSettings.Load();
        private MemoryManager _memory = new();
        private OllamaClient _ollama;
        private AutoUpdater _updater = new();
        private List<OllamaClient.ChatMessage> _history = new();
        private CancellationTokenSource _chatCts = new();

        // UI elements (set in InitializeComponent in .xaml or code-behind)
        private RichTextBox _chatDisplay = null!;
        private TextBox _chatInput = null!;
        private Button _sendBtn = null!;
        private TextBlock _statusBar = null!;
        private TextBox _imgPrompt = null!;
        private Button _imgGenBtn = null!;
        private TextBlock _imgStatus = null!;

        public MainWindow()
        {
            _ollama = new OllamaClient(_settings.OllamaHost);
            InitializeComponent();
            SetupWindowContent();
            _ = PostInitAsync();
        }

        private async Task PostInitAsync()
        {
            await CheckOllamaConnectionAsync();

            if (_settings.AutoUpdate)
            {
                // Check for updates after 5s delay (don't block startup)
                await Task.Delay(5000);
                await CheckForUpdatesAsync(silent: true);
            }
        }

        private void SetupWindowContent()
        {
            Title = $"🍪 CookieAI v{AppVersion}";
            Width  = 1000;
            Height = 700;

            var tab = new TabControl();

            tab.Items.Add(BuildChatTab());
            tab.Items.Add(BuildImageTab());
            tab.Items.Add(BuildSettingsTab());

            var statusPanel = new DockPanel();
            DockPanel.SetDock(tab, Dock.Top);
            statusPanel.Children.Add(tab);

            _statusBar = new TextBlock
            {
                Text       = "Ready.",
                Margin     = new Thickness(5),
                Foreground = new SolidColorBrush(Colors.Gray),
                FontSize   = 11,
            };
            DockPanel.SetDock(_statusBar, Dock.Bottom);
            statusPanel.Children.Add(_statusBar);

            Content = statusPanel;

            AppendChat("🍪 CookieAI — your private local AI assistant\n");
            AppendChat("All AI runs locally via Ollama. No telemetry. No Microsoft. No cloud.\n");
            AppendChat(new string('─', 50) + "\n\n");
        }

        // ── Chat Tab ──────────────────────────────────────────────────────────

        private TabItem BuildChatTab()
        {
            _chatDisplay = new RichTextBox
            {
                IsReadOnly = true,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                FontFamily = new FontFamily("Consolas"),
                FontSize   = 13,
                Background = new SolidColorBrush(Color.FromRgb(18, 18, 18)),
                Foreground = new SolidColorBrush(Colors.LightGray),
            };

            _chatInput = new TextBox
            {
                Height      = 40,
                FontSize    = 13,
                Background  = new SolidColorBrush(Color.FromRgb(30, 30, 30)),
                Foreground  = new SolidColorBrush(Colors.White),
                CaretBrush  = new SolidColorBrush(Colors.White),
            };
            _chatInput.KeyDown += (s, e) =>
            {
                if (e.Key == Key.Enter && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
                {
                    e.Handled = true;
                    _ = SendMessageAsync();
                }
            };

            _sendBtn = new Button { Content = "Send", Width = 70, Height = 40 };
            _sendBtn.Click += async (s, e) => await SendMessageAsync();

            var clearBtn = new Button { Content = "Clear", Width = 60, Height = 40, Margin = new Thickness(4, 0, 0, 0) };
            clearBtn.Click += (s, e) =>
            {
                _history.Clear();
                _chatDisplay.Document.Blocks.Clear();
                AppendChat("Chat cleared.\n\n");
            };

            var inputRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 4, 0, 0) };
            inputRow.Children.Add(_chatInput);
            _chatInput.MinWidth = 700;
            inputRow.Children.Add(_sendBtn);
            inputRow.Children.Add(clearBtn);

            var panel = new DockPanel();
            DockPanel.SetDock(inputRow, Dock.Bottom);
            panel.Children.Add(inputRow);
            panel.Children.Add(_chatDisplay);

            return new TabItem { Header = "💬 Chat", Content = panel };
        }

        private async Task SendMessageAsync()
        {
            var text = _chatInput.Text.Trim();
            if (string.IsNullOrEmpty(text)) return;
            _chatInput.Text = "";

            var filterResult = ContentFilter.Check(text, _settings.AdultFilter);
            if (!filterResult.Allowed)
            {
                AppendChat($"🚫 {filterResult.Reason}\n\n", Colors.OrangeRed);
                return;
            }

            AppendChat($"You: {text}\n", Colors.CornflowerBlue);
            AppendChat("CookieGPT: ", Colors.LightGreen);
            _sendBtn.IsEnabled = false;

            var systemPrompt = "You are CookieGPT, a helpful private AI assistant running on CookieOS (Windows). " +
                               "All processing is local. Be direct and concise.";

            if (_settings.MemoryEnabled && _settings.EnterpriseEnabled)
            {
                var ctx = _memory.GetContext();
                if (!string.IsNullOrEmpty(ctx))
                    systemPrompt += $"\n\nUser context:\n{ctx}";
            }

            var messages = new List<OllamaClient.ChatMessage>
            {
                new("system", systemPrompt),
            };
            messages.AddRange(_history);
            messages.Add(new("user", text));

            _chatCts = new CancellationTokenSource();
            var responseBuffer = new StringBuilder();

            try
            {
                await _ollama.ChatStreamAsync(
                    messages,
                    _settings.Model,
                    chunk =>
                    {
                        responseBuffer.Append(chunk);
                        Dispatcher.Invoke(() => AppendChat(chunk, Colors.LightGreen));
                    },
                    _chatCts.Token);

                _history.Add(new("user", text));
                _history.Add(new("assistant", responseBuffer.ToString()));
                if (_history.Count > 40) _history.RemoveAt(0);

                if (_settings.MemoryEnabled && _settings.EnterpriseEnabled)
                    _memory.Set("last_topic", text[..Math.Min(100, text.Length)]);
            }
            catch (OperationCanceledException)
            {
                AppendChat("[Cancelled]", Colors.Gray);
            }
            catch (Exception ex)
            {
                AppendChat($"\n[Error: {ex.Message}]\n", Colors.OrangeRed);
                ShowStatus("Ollama connection failed. Check Settings.");
            }
            finally
            {
                Dispatcher.Invoke(() =>
                {
                    AppendChat("\n\n");
                    _sendBtn.IsEnabled = true;
                });
            }
        }

        // ── Image Tab ─────────────────────────────────────────────────────────

        private TabItem BuildImageTab()
        {
            _imgPrompt  = new TextBox { Height = 70, TextWrapping = TextWrapping.Wrap,
                                        AcceptsReturn = true, Margin = new Thickness(0, 0, 0, 8) };
            _imgGenBtn  = new Button { Content = "Generate Image", Height = 36, Margin = new Thickness(0, 0, 0, 8) };
            _imgStatus  = new TextBlock { Text = "Ready.", Foreground = new SolidColorBrush(Colors.Gray) };

            _imgGenBtn.Click += async (s, e) => await GenerateImageAsync();

            var panel = new StackPanel { Margin = new Thickness(10) };
            panel.Children.Add(new TextBlock { Text = "🖼 CookieFocus — Local Image Generation",
                                               FontSize = 16, Margin = new Thickness(0, 0, 0, 8) });
            panel.Children.Add(new TextBlock { Text = "All images generated locally. NSFW filter active.",
                                               Foreground = new SolidColorBrush(Colors.Gray),
                                               Margin = new Thickness(0, 0, 0, 12) });
            panel.Children.Add(new Label { Content = "Prompt:" });
            panel.Children.Add(_imgPrompt);
            panel.Children.Add(_imgGenBtn);
            panel.Children.Add(_imgStatus);

            return new TabItem { Header = "🖼 Image Gen", Content = panel };
        }

        private async Task GenerateImageAsync()
        {
            var prompt = _imgPrompt.Text.Trim();
            if (string.IsNullOrEmpty(prompt)) { _imgStatus.Text = "⚠ Enter a prompt."; return; }

            var filter = ContentFilter.Check(prompt, _settings.AdultFilter);
            if (!filter.Allowed) { _imgStatus.Text = $"🚫 {filter.Reason}"; return; }

            _imgStatus.Text = "⏳ Generating...";
            _imgGenBtn.IsEnabled = false;

            try
            {
                using var http = new HttpClient();
                var body = JsonSerializer.Serialize(new { prompt, style = "Realistic" });
                var r = await http.PostAsync(
                    $"{_settings.OllamaHost}/fooocus/generate",
                    new StringContent(body, Encoding.UTF8, "application/json"));

                if (r.IsSuccessStatusCode)
                {
                    var result = JsonDocument.Parse(await r.Content.ReadAsStringAsync());
                    var filename = result.RootElement.TryGetProperty("filename", out var fn)
                        ? fn.GetString() : "output.png";
                    _imgStatus.Text = $"✓ Saved: {filename}";
                }
                else
                {
                    _imgStatus.Text = $"⚠ Generation failed ({(int)r.StatusCode})";
                }
            }
            catch (Exception ex)
            {
                _imgStatus.Text = $"⚠ Error: {ex.Message}";
            }
            finally
            {
                _imgGenBtn.IsEnabled = true;
            }
        }

        // ── Settings Tab ──────────────────────────────────────────────────────

        private TabItem BuildSettingsTab()
        {
            var hostInput = new TextBox { Text = _settings.OllamaHost };
            var modelInput = new TextBox { Text = _settings.Model };
            var adultSwitch = new CheckBox { Content = "Block adult content (18+ filter)", IsChecked = _settings.AdultFilter };
            var memSwitch = new CheckBox { Content = "Persistent memory (Enterprise)", IsChecked = _settings.MemoryEnabled };
            var autoUpdateSwitch = new CheckBox { Content = "Auto-check for updates (GitHub)", IsChecked = _settings.AutoUpdate };
            var ccInput = new TextBox { Text = _settings.CookieCloudServer };

            var saveBtn = new Button { Content = "Save Settings", Height = 36, Margin = new Thickness(0, 16, 0, 0) };
            saveBtn.Click += (s, e) =>
            {
                _settings.OllamaHost     = hostInput.Text.Trim();
                _settings.Model          = modelInput.Text.Trim();
                _settings.AdultFilter    = adultSwitch.IsChecked ?? true;
                _settings.MemoryEnabled  = memSwitch.IsChecked ?? false;
                _settings.AutoUpdate     = autoUpdateSwitch.IsChecked ?? true;
                _settings.CookieCloudServer = ccInput.Text.Trim();
                _settings.Save();
                _ollama = new OllamaClient(_settings.OllamaHost);
                ShowStatus("✓ Settings saved.");
            };

            var updateBtn = new Button { Content = "Check for Updates Now", Height = 36, Margin = new Thickness(0, 4, 0, 0) };
            updateBtn.Click += async (s, e) => await CheckForUpdatesAsync(silent: false);

            var clearMemBtn = new Button { Content = "Clear Memory", Height = 36, Margin = new Thickness(0, 4, 0, 0) };
            clearMemBtn.Click += (s, e) => { _memory.Clear(); ShowStatus("Memory cleared."); };

            var panel = new StackPanel { Margin = new Thickness(20) };
            void AddRow(string label, UIElement control)
            {
                panel.Children.Add(new Label { Content = label });
                panel.Children.Add(control);
            }

            panel.Children.Add(new TextBlock { Text = "⚙ Settings", FontSize = 16,
                                               Margin = new Thickness(0, 0, 0, 16) });
            AddRow("Ollama host (Tailscale IP):", hostInput);
            AddRow("Chat model:", modelInput);
            AddRow("CookieCloud server (optional):", ccInput);
            panel.Children.Add(adultSwitch);
            panel.Children.Add(memSwitch);
            panel.Children.Add(autoUpdateSwitch);
            panel.Children.Add(saveBtn);
            panel.Children.Add(updateBtn);
            panel.Children.Add(clearMemBtn);
            panel.Children.Add(new TextBlock
            {
                Text = $"\nCookieAI v{AppVersion} | Open-source edition | No telemetry | All AI local",
                Foreground = new SolidColorBrush(Colors.Gray),
                Margin = new Thickness(0, 16, 0, 0),
                TextWrapping = TextWrapping.Wrap,
            });

            return new TabItem { Header = "⚙ Settings", Content = new ScrollViewer { Content = panel } };
        }

        // ── Helpers ───────────────────────────────────────────────────────────

        private async Task CheckOllamaConnectionAsync()
        {
            var available = await _ollama.IsAvailableAsync();
            ShowStatus(available
                ? "✓ Ollama connected"
                : "⚠ Ollama not reachable. Check Settings → Ollama host and Tailscale.");
        }

        private async Task CheckForUpdatesAsync(bool silent)
        {
            ShowStatus("Checking for updates...");
            var update = await _updater.CheckAsync(AppVersion);
            if (update != null)
            {
                ShowStatus($"Update available: v{update.LatestVersion}");
                if (!silent)
                {
                    var result = MessageBox.Show(
                        $"CookieOS update available!\n\nCurrent: v{update.CurrentVersion}\nLatest:  v{update.LatestVersion}\n\n{update.ReleaseNotes}\n\nOpen GitHub to download?",
                        "CookieAI Update",
                        MessageBoxButton.YesNo,
                        MessageBoxImage.Information);

                    if (result == MessageBoxResult.Yes)
                        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                        {
                            FileName = update.HtmlUrl,
                            UseShellExecute = true,
                        });
                }
            }
            else if (!silent)
            {
                ShowStatus($"CookieAI v{AppVersion} is up to date.");
            }
        }

        private void AppendChat(string text, Color? color = null)
        {
            Dispatcher.Invoke(() =>
            {
                var para = _chatDisplay.Document.Blocks.LastBlock as Paragraph
                           ?? new Paragraph();

                if (!_chatDisplay.Document.Blocks.Contains(para))
                    _chatDisplay.Document.Blocks.Add(para);

                var run = new Run(text);
                if (color.HasValue)
                    run.Foreground = new SolidColorBrush(color.Value);

                para.Inlines.Add(run);
                _chatDisplay.ScrollToEnd();
            });
        }

        private void ShowStatus(string msg)
        {
            Dispatcher.Invoke(() => _statusBar.Text = msg);
        }
    }
}
