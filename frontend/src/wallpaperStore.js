// Store uploaded images locally without filling localStorage's small quota.
function openStore() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('asrar-wallpapers', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('images', { keyPath: 'id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Local wallpaper storage is unavailable.'));
  });
}
async function transaction(mode, operation) {
  const db = await openStore();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('images', mode);
      const request = operation(tx.objectStore('images'));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = () => reject(new Error('Could not save wallpapers. Local storage may be full.'));
      tx.onabort = () => reject(new Error('Wallpaper storage was interrupted.'));
    });
  } finally { db.close(); }
}
export const readWallpapers = () => transaction('readonly', store => store.getAll());
export const saveWallpaper = image => transaction('readwrite', store => store.put(image));
export const deleteWallpaper = id => transaction('readwrite', store => store.delete(id));
export async function importWallpaper(file) {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Choose a JPG, PNG, or WebP image.');
  if (file.size > 12 * 1024 * 1024) throw new Error('Choose an image smaller than 12 MB.');
  const image = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('This image could not be read. Try another file.'));
    reader.readAsDataURL(file);
  });
  const decoded = new Image();
  decoded.src = image;
  try { await decoded.decode(); } catch { throw new Error('This file is not a readable image.'); }
  const record = { id: `upload-${crypto.randomUUID()}`, name: file.name.replace(/\.[^.]+$/, '').slice(0, 80) || 'My wallpaper', image, custom: true };
  await saveWallpaper(record);
  return record;
}
