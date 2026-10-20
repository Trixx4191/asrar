const { app, BrowserWindow, ipcMain, shell, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { spawn } = require("child_process");

let mainWindow;
let pythonProcess;
const projectRoot = path.resolve(__dirname, "../../");
const desktopURL = app.isPackaged ? "http://127.0.0.1:8000/desktop/" : "http://localhost:5173";

function health() {
  return new Promise(resolve => {
    const request = http.get("http://127.0.0.1:8000/health", response => {
      response.resume(); resolve(response.statusCode === 200);
    });
    request.setTimeout(600, () => { request.destroy(); resolve(false); });
    request.on("error", () => resolve(false));
  });
}

async function startPythonBackend() {
  if (await health()) return;
  const venv = path.join(projectRoot, process.platform === "win32" ? "venv/Scripts/python.exe" : "venv/bin/python");
  pythonProcess = spawn(fs.existsSync(venv) ? venv : process.platform === "win32" ? "python" : "python3", [path.join(projectRoot, "main.py")], { cwd: projectRoot });
  pythonProcess.stdout.on("data", d => console.log(`[backend] ${d}`));
  pythonProcess.stderr.on("data", d => console.error(`[backend] ${d}`));
  pythonProcess.on("error", e => console.error("Backend could not start:", e.message));
  for (let attempt = 0; attempt < 40; attempt++) {
    if (await health()) return;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

function openSecureLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) throw new Error("Invalid external address");
    return shell.openExternal(url.href);
  } catch { return Promise.reject(new Error("Only secure web links may be opened")); }
}

function trustedFrame(event) {
  const url = event.senderFrame?.url;
  return url && new URL(url).origin === new URL(desktopURL).origin;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 920, minWidth: 760, minHeight: 600,
    titleBarStyle: "hidden", backgroundColor: "#101114",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    openSecureLink(url).catch(() => {});
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (new URL(url).origin !== new URL(desktopURL).origin) event.preventDefault();
  });
  mainWindow.loadURL(desktopURL);
}

app.whenReady().then(async () => { await startPythonBackend(); createWindow(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
app.on("before-quit", () => { if (pythonProcess) pythonProcess.kill(); });
ipcMain.on("minimize", event => { if (trustedFrame(event)) mainWindow.minimize(); });
ipcMain.on("maximize", event => { if (trustedFrame(event)) mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize(); });
ipcMain.on("close", event => { if (trustedFrame(event)) mainWindow.close(); });
ipcMain.handle("open-external", (event, url) => {
  if (!trustedFrame(event)) throw new Error("Untrusted window");
  return openSecureLink(url);
});
ipcMain.handle("choose-workspace", async event => {
  if (!trustedFrame(event)) throw new Error("Untrusted window");
  const result = await dialog.showOpenDialog(mainWindow, { title: "Choose an Asrār workspace", properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});
