using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("PLDL Uninstall")]
[assembly: AssemblyDescription("Полное удаление PLDL")]
[assembly: AssemblyProduct("PLDL")]
[assembly: AssemblyCompany("PLDL")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]

namespace PldlUninstall
{
    internal static class Program
    {
        private const string AppName = "PLDL";
        private const string UninstallKey =
            @"Software\Microsoft\Windows\CurrentVersion\Uninstall\{16896A40-C283-4FDF-9EAB-3DE218F90612}_is1";

        [STAThread]
        private static int Main(string[] args)
        {
            bool silent = HasArg(args, "/SILENT") || HasArg(args, "/VERYSILENT");
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string roamingAppData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);

            string installDir = FindInstallDir(localAppData);
            string defaultInstallDir = Path.Combine(localAppData, @"Programs\PLDL");
            string fallbackData = Path.Combine(localAppData, AppName);
            string roamingData = Path.Combine(roamingAppData, AppName);
            string startMenu = Path.Combine(programs, AppName);
            string desktopShortcut = Path.Combine(desktop, AppName + ".lnk");

            bool installed = installDir != null && Directory.Exists(installDir);
            bool hasLeftovers =
                Directory.Exists(defaultInstallDir) ||
                Directory.Exists(fallbackData) ||
                Directory.Exists(roamingData) ||
                Directory.Exists(startMenu) ||
                File.Exists(desktopShortcut) ||
                Registry.CurrentUser.OpenSubKey(UninstallKey) != null;

            if (!installed && !hasLeftovers)
            {
                if (!silent)
                {
                    MessageBox.Show(
                        "PLDL не найден.\n\nПохоже, приложение уже удалено.",
                        "Удаление PLDL",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                }
                return 0;
            }

            if (!silent)
            {
                DialogResult answer = MessageBox.Show(
                    "Удалить PLDL с компьютера?\n\n" +
                    "Будут удалены:\n" +
                    "• приложение PLDL;\n" +
                    "• ярлыки на рабочем столе и в меню «Пуск»;\n" +
                    "• настройки, журнал и кэш;\n" +
                    "• запись в списке установленных программ.\n\n" +
                    "Скачанная музыка в папке загрузки не удаляется.",
                    "Удаление PLDL",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);

                if (answer != DialogResult.Yes)
                {
                    return 2;
                }
            }

            StopAppProcesses();
            RunVendorUninstaller(installDir);

            bool ok = true;
            if (!string.IsNullOrEmpty(installDir))
            {
                ok &= DeleteDirectory(installDir);
            }
            if (!string.Equals(installDir, defaultInstallDir, StringComparison.OrdinalIgnoreCase))
            {
                ok &= DeleteDirectory(defaultInstallDir);
            }

            ok &= DeleteDirectory(fallbackData);
            ok &= DeleteDirectory(roamingData);
            ok &= DeleteDirectory(startMenu);
            ok &= DeleteFile(desktopShortcut);
            ok &= DeleteFile(Path.Combine(programs, AppName + ".lnk"));

            DeleteShortcutPattern(desktop, AppName + "*.lnk");
            DeleteShortcutPattern(desktop, "Удалить PLDL*.lnk");

            try
            {
                Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            }
            catch
            {
                ok = false;
            }

            if (!silent)
            {
                string message;
                if (ok)
                {
                    message =
                        "PLDL удалён.\n\n" +
                        "Скачанная музыка в папке загрузки не удалялась.";
                }
                else
                {
                    message =
                        "PLDL удалён не полностью.\n\n" +
                        "Закройте приложение и запустите удаление ещё раз. Если не поможет, удалите папку:\n" +
                        (installDir ?? defaultInstallDir);
                }
                MessageBox.Show(
                    message,
                    "Удаление PLDL",
                    MessageBoxButtons.OK,
                    ok ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            }

            return ok ? 0 : 1;
        }

        private static bool HasArg(string[] args, string name)
        {
            foreach (string arg in args)
            {
                if (string.Equals(arg, name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        private static string FindInstallDir(string localAppData)
        {
            string fromRegistry = null;
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
                {
                    if (key != null)
                    {
                        object value = key.GetValue("InstallLocation");
                        if (value != null)
                        {
                            fromRegistry = Convert.ToString(value).Trim().TrimEnd('\\');
                        }
                    }
                }
            }
            catch
            {
                // Fall back to the default location below.
            }

            if (!string.IsNullOrEmpty(fromRegistry) && Directory.Exists(fromRegistry))
            {
                return fromRegistry;
            }

            return Path.Combine(localAppData, @"Programs\PLDL");
        }

        private static void StopAppProcesses()
        {
            foreach (Process process in Process.GetProcessesByName(AppName))
            {
                try
                {
                    if (!process.HasExited)
                    {
                        process.Kill();
                        process.WaitForExit(5000);
                    }
                }
                catch
                {
                    // The app may have already closed.
                }
                finally
                {
                    process.Dispose();
                }
            }

            Thread.Sleep(500);
        }

        private static void RunVendorUninstaller(string installDir)
        {
            if (string.IsNullOrEmpty(installDir))
            {
                return;
            }

            string uninstaller = Path.Combine(installDir, "unins000.exe");
            if (!File.Exists(uninstaller))
            {
                return;
            }

            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = uninstaller;
                info.Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART";
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.WorkingDirectory = installDir;

                using (Process process = Process.Start(info))
                {
                    if (process != null)
                    {
                        process.WaitForExit(120000);
                    }
                }
            }
            catch
            {
                // Manual cleanup below will remove the remaining files.
            }

            for (int i = 0; i < 40 && Directory.Exists(installDir); i++)
            {
                Thread.Sleep(250);
            }
        }

        private static bool DeleteDirectory(string path)
        {
            if (string.IsNullOrEmpty(path) || !Directory.Exists(path))
            {
                return true;
            }

            for (int i = 0; i < 30; i++)
            {
                try
                {
                    Directory.Delete(path, true);
                    return true;
                }
                catch
                {
                    Thread.Sleep(300);
                }
            }

            return !Directory.Exists(path);
        }

        private static bool DeleteFile(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                return true;
            }

            for (int i = 0; i < 10; i++)
            {
                try
                {
                    File.Delete(path);
                    return true;
                }
                catch
                {
                    Thread.Sleep(200);
                }
            }

            return !File.Exists(path);
        }

        private static void DeleteShortcutPattern(string directory, string pattern)
        {
            try
            {
                foreach (string path in Directory.GetFiles(directory, pattern))
                {
                    DeleteFile(path);
                }
            }
            catch
            {
                // Shortcut cleanup is best-effort.
            }
        }
    }
}
