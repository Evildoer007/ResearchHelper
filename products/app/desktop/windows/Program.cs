using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Adapted from the read-only OH host. ResearchHelper owns its service and store.

namespace ResearchHelper.Windows;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Contains("--check-webview2", StringComparer.Ordinal))
        {
            try
            {
                Console.WriteLine(CoreWebView2Environment.GetAvailableBrowserVersionString());
            }
            catch (Exception error)
            {
                Console.Error.WriteLine($"WebView2 Runtime不可用：{error.Message}");
                Environment.ExitCode = 1;
            }
            return;
        }
        ApplicationConfiguration.Initialize();
        var resources = Path.Combine(AppContext.BaseDirectory, "Resources");
        var backend = Path.Combine(resources, "backend", "researchhelper-server", "researchhelper-server.exe");
        if (!File.Exists(backend))
        {
            MessageBox.Show("找不到内置应用服务。", "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ResearchHelper", "research-v1");
        Directory.CreateDirectory(state);
        using var process = new Process
        {
            StartInfo = new ProcessStartInfo(backend)
            {
                WorkingDirectory = Path.GetDirectoryName(backend)!,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
                CreateNoWindow = true,
            }
        };
        var started = false;
        Task<string>? backendErrorTask = null;
        try
        {
            process.StartInfo.Environment["PYTHONUTF8"] = "1";
            process.StartInfo.Environment["PYTHONIOENCODING"] = "utf-8";
            process.StartInfo.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
            process.StartInfo.Environment["RESEARCHHELPER_STORE"] = state;
            process.Start();
            started = true;
            backendErrorTask = process.StandardError.ReadToEndAsync();
            // Before Application.Run there is no WinForms synchronization
            // context. An awaited Main would resume on an MTA worker thread.
            var url = WaitForUrl(process, TimeSpan.FromSeconds(45)).GetAwaiter().GetResult();
            _ = DrainOutput(process.StandardOutput);
            Application.Run(new MainForm(url, state));
        }
        catch (Exception error)
        {
            if (started && !process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
            }
            var detail = error.Message;
            if (backendErrorTask is not null)
            {
                var backendError = backendErrorTask.GetAwaiter().GetResult().Trim();
                if (backendError.Length > 4000)
                    backendError = backendError[^4000..];
                if (!string.IsNullOrWhiteSpace(backendError))
                    detail += $"\n\n后端错误：\n{backendError}";
            }
            MessageBox.Show($"应用服务未能启动。\n{detail}", "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            if (started && !process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
            }
        }
    }

    private static async Task DrainOutput(StreamReader output)
    {
        try
        {
            while (await output.ReadLineAsync().ConfigureAwait(false) is not null) { }
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
    }

    private static async Task<string> WaitForUrl(Process process, TimeSpan timeout)
    {
        using var cancellation = new CancellationTokenSource(timeout);
        while (!process.HasExited && !cancellation.Token.IsCancellationRequested)
        {
            var line = await process.StandardOutput.ReadLineAsync(cancellation.Token);
            if (string.IsNullOrWhiteSpace(line)) continue;
            try
            {
                using var message = JsonDocument.Parse(line);
                var root = message.RootElement;
                if (root.TryGetProperty("ready", out var ready) && ready.ValueKind == JsonValueKind.True
                    && root.TryGetProperty("url", out var address)
                    && Uri.TryCreate(address.GetString(), UriKind.Absolute, out var url)
                    && url.Scheme == "http" && url.Host == "127.0.0.1" && url.Port > 0
                    && string.IsNullOrEmpty(url.UserInfo) && url.AbsolutePath == "/"
                    && string.IsNullOrEmpty(url.Query) && string.IsNullOrEmpty(url.Fragment))
                    return url.AbsoluteUri;
            }
            catch (JsonException) { }
        }
        throw new InvalidOperationException("后端未在45秒内提供本机地址。");
    }
}

internal sealed class MainForm : Form
{
    private const int WmSysCommand = 0x0112;
    private const int ScaleUpCommand = 0x1F10;
    private const int ScaleDownCommand = 0x1F20;
    private const int ScaleResetCommand = 0x1F30;
    private const uint MfString = 0x0000;
    private const uint MfSeparator = 0x0800;
    private const uint MfByCommand = 0x0000;
    private const uint MfEnabled = 0x0000;
    private const uint MfGrayed = 0x0001;
    private const int DwmwaUseImmersiveDarkMode = 20;
    private const int DwmwaSystemBackdropType = 38;
    private const int DwmsbtMainWindow = 2;

    private static readonly double[] ScaleSteps = [0.8, 0.9, 1.0, 1.1, 1.25, 1.4];
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.Transparent };
    private readonly string uiPreferencesPath;
    private readonly Uri appOrigin;
    private readonly HashSet<Form> reportWindows = [];
    private readonly SemaphoreSlim uiScaleScriptLock = new(1, 1);
    private double uiScale;
    private bool webViewReady;
    private bool systemMenuInstalled;
    private bool nativeBackdropEnabled;
    private string surfaceTheme = "light";
    private string? uiScaleScriptId;

    internal MainForm(string url, string stateDirectory)
    {
        appOrigin = new Uri(url);
        Text = "ResearchHelper";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        AutoScaleDimensions = new SizeF(96, 96);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(1320, 860);
        StartPosition = FormStartPosition.Manual;
        KeyPreview = true;
        BackColor = Color.FromArgb(247, 247, 247);
        uiPreferencesPath = Path.Combine(stateDirectory, "ui-preferences.json");
        uiScale = UIScalePreferences.Load(uiPreferencesPath, ScaleSteps);
        Controls.Add(browser);
        Shown += async (_, _) =>
        {
            try
            {
                var webViewData = Path.Combine(stateDirectory, "webview2");
                Directory.CreateDirectory(webViewData);
                var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: webViewData);
                await browser.EnsureCoreWebView2Async(environment);
                browser.CoreWebView2.Settings.IsZoomControlEnabled = false;
                browser.ZoomFactor = uiScale;
                await UpdateScaleBootstrapScript();
                browser.CoreWebView2.WebMessageReceived += HandleWebMessage;
                browser.CoreWebView2.NewWindowRequested += OpenReportWindow;
                browser.CoreWebView2.NavigationStarting += (_, navigation) =>
                {
                    navigation.Cancel = !Uri.TryCreate(navigation.Uri, UriKind.Absolute, out var target)
                        || !(IsSameOrigin(target) || target.AbsoluteUri == "about:blank");
                };
                browser.CoreWebView2.NavigationCompleted += (_, _) => SyncScaleToPage();
                webViewReady = true;
                var startupUrl = new UriBuilder(url);
                var query = startupUrl.Query.TrimStart('?');
                startupUrl.Query = (string.IsNullOrEmpty(query) ? "" : query + "&")
                    + "app_startup=" + Guid.NewGuid().ToString("N");
                browser.CoreWebView2.Navigate(startupUrl.Uri.AbsoluteUri);
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    $"WebView2 Runtime不可用，ResearchHelper无法显示界面。\n请安装Microsoft Edge WebView2 Runtime后重试。\n\n{error.Message}",
                    "ResearchHelper",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                Close();
            }
        };
    }

    protected override void OnLoad(EventArgs eventArgs)
    {
        base.OnLoad(eventArgs);
        // The handle now has the selected monitor's DPI. Match the macOS
        // logical content size, then use a large, centered working-area fit.
        // This runs only on first display; later user resizing stays untouched.
        var workArea = Screen.FromHandle(Handle).WorkingArea;
        var scale = DeviceDpi / 96.0;
        var preferred = SizeFromClientSize(new Size(
            (int)Math.Round(1320 * scale), (int)Math.Round(860 * scale)));
        Bounds = InitialWindowBounds(workArea, preferred, DeviceDpi);
    }

    internal static Rectangle InitialWindowBounds(Rectangle workArea, Size preferred, int dpi)
    {
        var margin = Math.Clamp((int)Math.Round(24 * dpi / 96.0), 0,
            Math.Min(workArea.Width, workArea.Height) / 4);
        var width = Math.Min(Math.Max(1, workArea.Width - 2 * margin),
            Math.Max(preferred.Width, (int)Math.Round(workArea.Width * 0.9)));
        var height = Math.Min(Math.Max(1, workArea.Height - 2 * margin),
            Math.Max(preferred.Height, (int)Math.Round(workArea.Height * 0.9)));
        return new Rectangle(workArea.Left + (workArea.Width - width) / 2,
            workArea.Top + (workArea.Height - height) / 2, width, height);
    }

    private bool IsSameOrigin(Uri url) => url.Scheme == appOrigin.Scheme
        && url.Host == appOrigin.Host && url.Port == appOrigin.Port && string.IsNullOrEmpty(url.UserInfo);

    private bool IsReportArtifact(Uri url) => IsSameOrigin(url)
        && (string.IsNullOrEmpty(url.Query) || url.Query == "?profile=standard" || url.Query == "?profile=detailed") && string.IsNullOrEmpty(url.Fragment)
        && (Regex.IsMatch(url.AbsolutePath, @"^/api/tasks/[A-Za-z0-9_-]+/report(?:/[1-9][0-9]*)?$")
            || Regex.IsMatch(url.AbsolutePath, @"^/reports/(?:semi|ipo|broker|consumer|medicine|internet)\.html$"));

    private async void OpenReportWindow(object? sender, CoreWebView2NewWindowRequestedEventArgs eventArgs)
    {
        eventArgs.Handled = true;
        if (!Uri.TryCreate(eventArgs.Uri, UriKind.Absolute, out var url)) return;
        if (!IsReportArtifact(url)) return;
        using var deferral = eventArgs.GetDeferral();
        var child = new Form { Text = "ResearchHelper报告预览", Width = 1000, Height = 800 };
        var view = new WebView2 { Dock = DockStyle.Fill };
        child.Controls.Add(view);
        reportWindows.Add(child);
        child.FormClosed += (_, _) => reportWindows.Remove(child);
        try
        {
            child.Show(this);
            // Share the authenticated profile without installing the main window's native bridge.
            await view.EnsureCoreWebView2Async(browser.CoreWebView2.Environment);
            if (child.IsDisposed) return;
            view.ZoomFactor = uiScale;
            view.CoreWebView2.Settings.IsWebMessageEnabled = false;
            view.CoreWebView2.NewWindowRequested += OpenReportWindow;
            view.CoreWebView2.NavigationStarting += (_, navigation) =>
            {
                if (!Uri.TryCreate(navigation.Uri, UriKind.Absolute, out var target))
                {
                    navigation.Cancel = true;
                    return;
                }
                navigation.Cancel = target.AbsoluteUri != "about:blank" && !IsReportArtifact(target);
            };
            eventArgs.NewWindow = view.CoreWebView2;
        }
        catch (Exception)
        {
            child.Close();
            MessageBox.Show(this, "报告窗口未能打开，请重试。", "ResearchHelper", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    protected override void OnHandleCreated(EventArgs eventArgs)
    {
        base.OnHandleCreated(eventArgs);
        nativeBackdropEnabled = TryEnableSystemBackdrop();
        ApplySurfaceTheme(surfaceTheme);
        if (systemMenuInstalled) return;
        var menu = GetSystemMenu(Handle, false);
        if (menu == IntPtr.Zero) return;
        AppendMenu(menu, MfSeparator, UIntPtr.Zero, string.Empty);
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleUpCommand), "放大\tCtrl++");
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleDownCommand), "缩小\tCtrl+-");
        AppendMenu(menu, MfString, new UIntPtr((uint)ScaleResetCommand), "实际大小\tCtrl+0");
        systemMenuInstalled = true;
        UpdateSystemMenuAvailability();
    }

    protected override void OnHandleDestroyed(EventArgs eventArgs)
    {
        systemMenuInstalled = false;
        nativeBackdropEnabled = false;
        base.OnHandleDestroyed(eventArgs);
    }

    protected override void OnPaintBackground(PaintEventArgs eventArgs)
    {
        if (!nativeBackdropEnabled) base.OnPaintBackground(eventArgs);
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if ((keyData & Keys.Control) == Keys.Control)
        {
            var keyCode = keyData & Keys.KeyCode;
            if (keyCode is Keys.Oemplus or Keys.Add)
            {
                ChangeScale(1);
                return true;
            }
            if (keyCode is Keys.OemMinus or Keys.Subtract)
            {
                ChangeScale(-1);
                return true;
            }
            if (keyCode is Keys.D0 or Keys.NumPad0)
            {
                ApplyScale(1.0, persist: true, notifyPage: true);
                return true;
            }
            if (keyCode == Keys.P)
            {
                _ = PrintCurrentReport();
                return true;
            }
        }
        return base.ProcessCmdKey(ref message, keyData);
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == WmSysCommand)
        {
            switch (message.WParam.ToInt32() & 0xFFF0)
            {
                case ScaleUpCommand:
                    ChangeScale(1);
                    return;
                case ScaleDownCommand:
                    ChangeScale(-1);
                    return;
                case ScaleResetCommand:
                    ApplyScale(1.0, persist: true, notifyPage: true);
                    return;
            }
        }
        base.WndProc(ref message);
    }

    private void HandleWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs eventArgs)
    {
        if (!Uri.TryCreate(eventArgs.Source, UriKind.Absolute, out var source)
            || !IsSameOrigin(source) || source.AbsolutePath != "/" && source.AbsolutePath != "/login/index.html") return;
        try
        {
            using var document = JsonDocument.Parse(eventArgs.WebMessageAsJson);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var type)) return;
            // Login may synchronize its theme, but cannot invoke file/print actions.
            if (source.AbsolutePath != "/" && type.GetString() != "set_theme") return;
            if (type.GetString() == "researchhelper_action" && root.TryGetProperty("action", out var action))
            {
                if (action.GetString() == "signed_out") CloseReportWindows();
                else if (action.GetString() == "print") _ = PrintCurrentReport();
                else if (action.GetString() == "save_binary" && root.TryGetProperty("name", out var binaryName)
                    && root.TryGetProperty("base64", out var encoded) && binaryName.ValueKind == JsonValueKind.String
                    && encoded.ValueKind == JsonValueKind.String) SaveBinary(binaryName.GetString()!, encoded.GetString()!);
                else if (action.GetString() == "save" && root.TryGetProperty("name", out var name)
                    && root.TryGetProperty("text", out var text) && name.ValueKind == JsonValueKind.String
                    && text.ValueKind == JsonValueKind.String) SaveReport(name.GetString()!, text.GetString()!);
                else if (action.GetString() == "zoom" && root.TryGetProperty("scale", out var zoom)
                    && zoom.TryGetDouble(out var requestedZoom) && NormalizeScale(requestedZoom) is double acceptedZoom)
                    ApplyScale(acceptedZoom, persist: true, notifyPage: true);
                return;
            }
            if (type.GetString() == "signed_out")
            {
                CloseReportWindows();
                return;
            }
            if (type.GetString() == "set_theme"
                && root.TryGetProperty("theme", out var theme)
                && theme.GetString() is string requestedTheme
                && requestedTheme is "light" or "dark")
            {
                ApplySurfaceTheme(requestedTheme);
                return;
            }
            if (type.GetString() != "set_ui_scale"
                || !root.TryGetProperty("scale", out var scale)
                || !scale.TryGetDouble(out var requested)
                || NormalizeScale(requested) is not double accepted) return;
            ApplyScale(accepted, persist: true, notifyPage: true);
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException)
        {
            // Ignore malformed messages from page scripts.
        }
    }

    private void CloseReportWindows()
    {
        foreach (var reportWindow in reportWindows.ToArray()) reportWindow.Close();
    }

    private void SaveBinary(string name, string encoded)
    {
        if (encoded.Length > 28_000_000 || string.IsNullOrEmpty(name) || name != Path.GetFileName(name) || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) return;
        var extension = Path.GetExtension(name).ToLowerInvariant();
        if (!new[] { ".pdf", ".docx", ".xlsx", ".csv" }.Contains(extension)) return;
        byte[] data;
        try { data = Convert.FromBase64String(encoded); } catch (FormatException) { return; }
        if (data.Length == 0 || data.Length > 20_000_000) return;
        if (extension == ".pdf" && !System.Text.Encoding.ASCII.GetString(data.Take(5).ToArray()).StartsWith("%PDF-")) return;
        if ((extension == ".docx" || extension == ".xlsx") && (data.Length < 2 || data[0] != 0x50 || data[1] != 0x4b)) return;
        using var dialog = new SaveFileDialog { FileName = name, Filter = "研究文件|*" + extension, DefaultExt = extension.TrimStart('.') };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        try { File.WriteAllBytes(dialog.FileName, data); }
        catch (IOException) { MessageBox.Show(this, "报告保存失败，请选择可写目录。", "ResearchHelper"); }
        catch (UnauthorizedAccessException) { MessageBox.Show(this, "没有该目录的写入权限。", "ResearchHelper"); }
    }
    private void SaveReport(string name, string text)
    {
        if (System.Text.Encoding.UTF8.GetByteCount(text) > 20_000_000) return;
        var filename = Path.GetFileName(name);
        if (string.IsNullOrEmpty(filename) || filename.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) return;
        var extension = Path.GetExtension(filename).TrimStart('.');
        using var dialog = new SaveFileDialog { FileName = filename, Filter = "研究文件|*.*", DefaultExt = extension };
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        try { File.WriteAllText(dialog.FileName, text, new System.Text.UTF8Encoding(false)); }
        catch (IOException) { MessageBox.Show(this, "报告保存失败，请选择可写目录。", "ResearchHelper"); }
        catch (UnauthorizedAccessException) { MessageBox.Show(this, "没有该目录的写入权限。", "ResearchHelper"); }
    }

    private async Task PrintCurrentReport()
    {
        try
        {
            if (!webViewReady) return;
            var raw = await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('#report-frame')?.src||''");
            var address = JsonSerializer.Deserialize<string>(raw);
            if (!Uri.TryCreate(address, UriKind.Absolute, out var url) || !IsReportArtifact(url)) return;
            var child = new Form { Text = "ResearchHelper报告打印", Width = 1000, Height = 800 };
            var view = new WebView2 { Dock = DockStyle.Fill };
            child.Controls.Add(view);
            reportWindows.Add(child);
            child.FormClosed += (_, _) => reportWindows.Remove(child);
            try
            {
                child.Show(this);
                await view.EnsureCoreWebView2Async(browser.CoreWebView2.Environment);
                if (child.IsDisposed) return;
                view.CoreWebView2.Settings.IsWebMessageEnabled = false;
                view.CoreWebView2.NewWindowRequested += (_, request) => request.Handled = true;
                view.CoreWebView2.NavigationStarting += (_, navigation) =>
                    navigation.Cancel = !Uri.TryCreate(navigation.Uri, UriKind.Absolute, out var target) || !IsReportArtifact(target);
                var printed = false;
                view.CoreWebView2.NavigationCompleted += async (_, navigation) =>
                {
                    if (printed || !navigation.IsSuccess || child.IsDisposed) return;
                    printed = true;
                    try
                    {
                        await view.CoreWebView2.ExecuteScriptAsync("window.dispatchEvent(new Event('beforeprint'))");
                        if (!child.IsDisposed) view.CoreWebView2.ShowPrintUI(CoreWebView2PrintDialogKind.System);
                    }
                    catch (Exception)
                    {
                        if (!child.IsDisposed) MessageBox.Show(child, "打印未完成，报告仍可保存。", "ResearchHelper");
                    }
                };
                view.CoreWebView2.Navigate(url.AbsoluteUri);
            }
            catch { child.Close(); throw; }
        }
        catch (Exception) { MessageBox.Show(this, "报告打印未能打开，请重试。", "ResearchHelper"); }
    }

    private static double? NormalizeScale(double value)
    {
        foreach (var step in ScaleSteps)
        {
            if (Math.Abs(step - value) < 0.0001) return step;
        }
        return null;
    }

    private static string JavascriptNumber(double value) => value.ToString("0.####", CultureInfo.InvariantCulture);

    private void ChangeScale(int offset)
    {
        var current = Array.FindIndex(ScaleSteps, step => Math.Abs(step - uiScale) < 0.0001);
        if (current < 0) current = Array.IndexOf(ScaleSteps, 1.0);
        var next = Math.Clamp(current + offset, 0, ScaleSteps.Length - 1);
        ApplyScale(ScaleSteps[next], persist: true, notifyPage: true);
    }

    private void ApplyScale(double scale, bool persist, bool notifyPage)
    {
        var accepted = NormalizeScale(scale);
        if (accepted is null) return;
        uiScale = accepted.Value;
        if (webViewReady) browser.ZoomFactor = uiScale;
        if (persist) UIScalePreferences.Save(uiPreferencesPath, uiScale);
        UpdateSystemMenuAvailability();
        if (!webViewReady) return;
        _ = UpdateScaleBootstrapScript();
        if (notifyPage) SyncScaleToPage();
    }

    private async Task UpdateScaleBootstrapScript()
    {
        await uiScaleScriptLock.WaitAsync();
        try
        {
            if (browser.CoreWebView2 is null) return;
            if (!string.IsNullOrEmpty(uiScaleScriptId)) browser.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(uiScaleScriptId);
            var scale = JavascriptNumber(uiScale);
            var material = nativeBackdropEnabled ? "system" : "fallback";
            uiScaleScriptId = await browser.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                $"(()=>{{if(window.top!==window||!['/','/login/index.html'].includes(location.pathname))return;window.webkit??={{}};window.webkit.messageHandlers??={{}};window.webkit.messageHandlers.researchhelper={{postMessage:value=>window.chrome.webview.postMessage({{...value,type:'researchhelper_action'}})}};const apply=()=>{{if(!document.documentElement)return;document.documentElement.dataset.nativeShell='windows';document.documentElement.dataset.nativeMaterial='{material}';document.documentElement.dataset.uiScale='{scale}';if(location.pathname==='/'&&new URL(location.href).searchParams.has('app_startup'))document.documentElement.classList.add('login-boot');}};if(document.documentElement)apply();else document.addEventListener('DOMContentLoaded',apply,{{once:true}});}})();"
            );
        }
        finally
        {
            uiScaleScriptLock.Release();
        }
    }

    private void SyncScaleToPage()
    {
        if (!webViewReady) return;
        _ = browser.CoreWebView2.ExecuteScriptAsync($"document.documentElement.dataset.uiScale='{JavascriptNumber(uiScale)}';document.documentElement.style.removeProperty('zoom');window.dispatchEvent(new Event('resize'));");
    }

    private void UpdateSystemMenuAvailability()
    {
        if (!IsHandleCreated) return;
        var menu = GetSystemMenu(Handle, false);
        if (menu == IntPtr.Zero) return;
        EnableMenuItem(menu, ScaleDownCommand, MfByCommand | (uiScale > ScaleSteps[0] ? MfEnabled : MfGrayed));
        EnableMenuItem(menu, ScaleUpCommand, MfByCommand | (uiScale < ScaleSteps[^1] ? MfEnabled : MfGrayed));
        DrawMenuBar(Handle);
    }

    private bool TryEnableSystemBackdrop()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 22621)) return false;
        try
        {
            var backdrop = DwmsbtMainWindow;
            if (DwmSetWindowAttribute(Handle, DwmwaSystemBackdropType, ref backdrop, sizeof(int)) != 0) return false;
            var margins = new Margins(-1);
            return DwmExtendFrameIntoClientArea(Handle, ref margins) == 0;
        }
        catch (DllNotFoundException)
        {
            return false;
        }
        catch (EntryPointNotFoundException)
        {
            return false;
        }
    }

    private void ApplySurfaceTheme(string theme)
    {
        surfaceTheme = theme == "dark" ? "dark" : "light";
        BackColor = surfaceTheme == "dark" ? Color.FromArgb(31, 31, 31) : Color.FromArgb(247, 247, 247);
        if (IsHandleCreated && OperatingSystem.IsWindowsVersionAtLeast(10, 0, 17763))
        {
            var dark = surfaceTheme == "dark" ? 1 : 0;
            _ = DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref dark, sizeof(int));
        }
        Invalidate();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Margins
    {
        internal int Left;
        internal int Right;
        internal int Top;
        internal int Bottom;

        internal Margins(int value)
        {
            Left = value;
            Right = value;
            Top = value;
            Bottom = value;
        }
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetSystemMenu(IntPtr window, bool revert);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool AppendMenu(IntPtr menu, uint flags, UIntPtr item, string text);

    [DllImport("user32.dll")]
    private static extern bool EnableMenuItem(IntPtr menu, int item, uint enable);

    [DllImport("user32.dll")]
    private static extern bool DrawMenuBar(IntPtr window);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);

    [DllImport("dwmapi.dll")]
    private static extern int DwmExtendFrameIntoClientArea(IntPtr window, ref Margins margins);
}

internal static class UIScalePreferences
{
    internal static double Load(string path, IReadOnlyList<double> allowed)
    {
        try
        {
            if (!File.Exists(path)) return 1.0;
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            if (!root.TryGetProperty("schema_version", out var version) || version.GetInt32() != 1
                || !root.TryGetProperty("ui_scale", out var scale) || !scale.TryGetDouble(out var value)) return 1.0;
            foreach (var step in allowed)
            {
                if (Math.Abs(step - value) < 0.0001) return step;
            }
            return 1.0;
        }
        catch (Exception)
        {
            return 1.0;
        }
    }

    internal static void Save(string path, double scale)
    {
        var temporary = path + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(temporary, JsonSerializer.Serialize(new { schema_version = 1, ui_scale = scale }, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporary, path, true);
        }
        catch (Exception)
        {
            try { if (File.Exists(temporary)) File.Delete(temporary); }
            catch { /* A later save can replace a stale temporary file. */ }
        }
    }
}
