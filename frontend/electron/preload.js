const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("asrar", {
  minimize: () => ipcRenderer.send("minimize"),
  maximize: () => ipcRenderer.send("maximize"),
  close:    () => ipcRenderer.send("close"),
  platform: process.platform,
});
