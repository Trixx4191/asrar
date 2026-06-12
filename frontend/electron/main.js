const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

let mainWindow;
let pythonProcess;

const API_URL = "http://127.0.0.1:8000";
const isDev = process.env.NODE_ENV === "development";

function startPythonBackend() {
  const scriptPath = path.join(__dirname, "../../main.py");
  pythonProcess = spawn("python", [scriptPath], {
    cwd: path.join(__dirname, "../.."),
  });

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

  if (isDev) {
    mainWindow.loadURL("http://localhost:5173");
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }
}

app.whenReady().then(() => {
  startPythonBackend();
  // Give backend a moment to start
  setTimeout(createWindow, 1500);
});

app.on("window-all-closed", () => {
  if (pythonProcess) pythonProcess.kill();
  if (process.platform !== "darwin") app.quit();
});

// IPC — window controls
ipcMain.on("minimize", () => mainWindow.minimize());
ipcMain.on("maximize", () => {
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});
ipcMain.on("close", () => {
  if (pythonProcess) pythonProcess.kill();
  mainWindow.close();
});
