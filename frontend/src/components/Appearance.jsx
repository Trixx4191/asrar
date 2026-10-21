import { useRef, useState } from 'react';
import { CHAT_THEMES, themeStyle } from '../appearance';

function Range({ label, value, min = 0, max, unit = '', onChange, help }) {
  return <label className="field-label appearance-range"><span>{label}<output>{value}{unit}</output></span>
    <input type="range" min={min} max={max} step="1" value={value} onChange={event => onChange(Number(event.target.value))} />
    {help && <small className="field-help">{help}</small>}
  </label>;
}

export default function Appearance({ appearance, onAppearanceChange, onAppearanceReset, wallpapers, wallpaper, onWallpaperChange, resolvedTheme, preferredMode, onWallpaperUpload, onWallpaperRemove, wallpaperStorageError }) {
  const fileInput = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [wallpaperError, setWallpaperError] = useState('');
  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setUploading(true); setWallpaperError('');
    try { await onWallpaperUpload(file); }
    catch (error) { setWallpaperError(error.message); }
    finally { setUploading(false); }
  }
  async function remove(id) {
    setWallpaperError('');
    try { await onWallpaperRemove(id); }
    catch (error) { setWallpaperError(error.message); }
  }
  const selectedWallpaper = wallpapers.find(item => item.id === wallpaper) || wallpapers[0];
  const selectedTheme = CHAT_THEMES.find(theme => theme.id === appearance.chatTheme) || CHAT_THEMES[0];
  const update = key => value => onAppearanceChange({ [key]: value });
  return <section className="connection-card appearance-settings">
    <div className="appearance-heading"><div><span className="eyebrow">MAKE IT FEEL LIKE YOU</span><h2>Chat appearance</h2></div><span className="appearance-saved">✓ Auto-saved</span></div>
    <p>Choose a palette, keep your favorite wallpaper, and fine-tune the details.</p>
    <div className="appearance-mode" role="group" aria-label="Color mode">
      {['light', 'dark', 'system'].map(mode => <button type="button" key={mode} aria-pressed={(appearance.mode || preferredMode || 'dark') === mode} onClick={() => update('mode')(mode)}>{mode === 'light' ? '☀ Light' : mode === 'dark' ? '☾ Dark' : '◐ System'}</button>)}
    </div>
    <div className="theme-preview" style={{ ...themeStyle(appearance, resolvedTheme), '--preview-wallpaper': `url("${selectedWallpaper.image}")` }} aria-label={`${selectedTheme.name} conversation preview`}>
      <div className="theme-preview-header"><span className="preview-avatar">◈</span><div>Asrār<small>Conversation preview · {resolvedTheme}</small></div><span className="preview-theme-name">{selectedTheme.name}</span></div>
      <div className="preview-conversation"><span className="preview-date">Today</span>
        <div className="preview-bubble incoming">A little clarity. A lot of possibility.<small>10:24</small></div>
        <div className="preview-bubble outgoing">This feels more like me.<small>10:25 ✓✓</small></div>
        <div className="preview-bubble incoming">Your space, down to the details.<small>10:25</small></div>
      </div>
      <div className="preview-composer"><span>Message Asrār…</span><span className="preview-send">↑</span></div>
    </div>
    <div className="appearance-section-title"><h3>Color theme</h3><span>Day & night palettes</span></div>
    <div className="chat-theme-grid" role="group" aria-label="Chat color theme">
      {CHAT_THEMES.map(theme => <button type="button" key={theme.id} aria-pressed={appearance.chatTheme === theme.id} className={`chat-theme-card ${appearance.chatTheme === theme.id ? 'selected' : ''}`} style={themeStyle({ ...appearance, chatTheme: theme.id }, resolvedTheme)} onClick={() => update('chatTheme')(theme.id)}>
        <span className="theme-miniature" aria-hidden="true"><i /><i /><i /><span>{appearance.chatTheme === theme.id ? '✓' : ''}</span></span>
        <strong>{theme.name}</strong><small>{theme.note}</small>
      </button>)}
    </div>
    <div className="appearance-section-title"><h3>Chat wallpaper</h3><span>Always sharp · no blur</span></div>
    <div className="appearance-wallpapers" role="group" aria-label="Chat background image">
      {wallpapers.map(item => <div className="wallpaper-option" key={item.id}><button type="button" aria-pressed={wallpaper === item.id} className={`appearance-wallpaper ${wallpaper === item.id ? 'selected' : ''}`} onClick={() => onWallpaperChange(item.id)}><img src={item.image} alt="" /><span>{item.name}{wallpaper === item.id ? ' ✓' : ''}</span></button>{item.custom && <button type="button" className="wallpaper-remove" aria-label={`Remove ${item.name}`} onClick={() => remove(item.id)}>×</button>}</div>)}
    </div>
    <div className="wallpaper-upload-row">
      <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" aria-label="Upload chat wallpaper" hidden onChange={upload} />
      <button type="button" className="secondary-btn" disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? 'Adding wallpaper…' : '+ Upload background'}</button>
      <small>JPG, PNG or WebP · up to 12 MB · saved on this device</small>
    </div>
    {(wallpaperError || wallpaperStorageError) && <p className="wallpaper-error" role="alert">{wallpaperError || wallpaperStorageError}</p>}
    <Range label="Wallpaper dimming" value={appearance.dim} max={100} unit="%" onChange={update('dim')} />
    <div className="appearance-section-title"><h3>Message bubbles</h3><span>Compact spacing, softer edges</span></div>
    <div className="appearance-control-grid">
      <Range label="Bubble opacity" value={appearance.bubbleOpacity} min={65} max={100} unit="%" onChange={update('bubbleOpacity')} help="Lower for lighter bubbles; higher for stronger contrast." />
      <Range label="Bubble corners" value={appearance.radius} min={8} max={26} unit="px" onChange={update('radius')} />
      <Range label="Message text size" value={appearance.fontSize} min={12} max={18} unit="px" onChange={update('fontSize')} />
    </div>
    <details className="appearance-advanced"><summary>Surrounding glass interface</summary>
      <Range label="Interface transparency" value={appearance.transparency} max={100} unit="%" onChange={update('transparency')} />
      <Range label="Interface glass blur" value={appearance.blur} max={40} unit="px" onChange={update('blur')} help="Only the surrounding interface is frosted. Chat wallpaper stays clear." />
    </details>
    <div className="appearance-footer"><small>Changes apply instantly and stay saved on this device.</small><button type="button" className="secondary-btn" onClick={onAppearanceReset}>Reset appearance</button></div>
  </section>;
}
