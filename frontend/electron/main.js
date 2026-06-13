const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

let mainWindow;
let pythonProcess;

function startPythonBackend() {
  const projectRoot = path.resolve(__dirname, "../../");
  const scriptPath = path.join(projectRoot, "main.py");
  console.log("[backend] starting:", scriptPath);
  pythonProcess = spawn("python3", [scriptPath], { cwd: projectRoot });
  pythonProcess.stdout.on("data", (d) => console.log(`[backend] ${d}`));
  pythonProcess.stderr.on("data", (d) => console.error(`[backend] ${d}`));
  pythonProcess.on("close", (code) => console.log(`[backend] exited: ${code}`));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: "hidden",
    backgroundColor: "#0a0a0f",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Always load Vite dev server in dev
  mainWindow.loadURL("http://localhost:5173");
}

app.whenReady().then(() => {
  startPythonBackend();
  setTimeout(createWindow, 1500);
});

app.on("window-all-closed", () => {
  if (pythonProcess) pythonProcess.kill();
  if (process.platform !== "darwin") app.quit();
});

ipcMain.on("minimize", () => mainWindow.minimize());
ipcMain.on("maximize", () => mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize());
ipcMain.on("close", () => { if (pythonProcess) pythonProcess.kill(); mainWindow.close(); });
