const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("asrar", {
  minimize: () => ipcRenderer.send("minimize"),
  maximize: () => ipcRenderer.send("maximize"),
  close:    () => ipcRenderer.send("close"),
  openExternal: url => ipcRenderer.invoke("open-external", url),
  chooseWorkspace: () => ipcRenderer.invoke("choose-workspace"),
  chooseContextFiles: () => ipcRenderer.invoke("choose-context-files"),
  platform: process.platform,
});
