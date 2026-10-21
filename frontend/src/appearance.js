// One palette source for the application, theme cards, and conversation preview.
export const CHAT_THEMES = [
  { id: 'rose', name: 'Midnight rose', note: 'Warm & familiar', tint: '#603441', dark: ['#211d25', '#593148', '#713f5a', '#ecc1d5'], light: ['#fffafc', '#f6dce8', '#edc8dc', '#824161'] },
  { id: 'ocean', name: 'Ocean', note: 'Clear & classic', tint: '#254b70', dark: ['#192633', '#20496b', '#28618a', '#a8d9ff'], light: ['#f9fcff', '#dcefff', '#c3e3ff', '#205e8d'] },
  { id: 'sage', name: 'Sage', note: 'A quieter space', tint: '#3e5c4c', dark: ['#1d2925', '#305440', '#3b6953', '#bfe2c7'], light: ['#f9fdf9', '#dfefdf', '#c6e4ce', '#366447'] },
  { id: 'violet', name: 'Dusk', note: 'Soft violet', tint: '#51416d', dark: ['#252131', '#493666', '#614a85', '#d9c6ff'], light: ['#fcfaff', '#eae1fa', '#dcd0f3', '#654493'] },
  { id: 'amber', name: 'Amber', note: 'A little warmth', tint: '#6d5033', dark: ['#29231e', '#60452c', '#7c5938', '#f5d3a5'], light: ['#fffdf8', '#f6e7d0', '#efd7b3', '#785129'] },
  { id: 'graphite', name: 'Graphite', note: 'Understated', tint: '#454854', dark: ['#24262c', '#3e424e', '#505563', '#d8dfed'], light: ['#fcfcfd', '#e6e9ef', '#d8dde7', '#4e586c'] },
];
export const DEFAULT_APPEARANCE = { transparency: 82, blur: 10, dim: 24, bubbleOpacity: 92, radius: 18, fontSize: 14, chatTheme: 'rose', mode: null };
const LIMITS = { transparency: [0, 100], blur: [0, 40], dim: [0, 100], bubbleOpacity: [65, 100], radius: [8, 26], fontSize: [12, 18] };
export function normalizeAppearance(saved) {
  const result = { ...DEFAULT_APPEARANCE };
  for (const [key, [min, max]] of Object.entries(LIMITS)) {
    if (typeof saved?.[key] === 'number' && Number.isFinite(saved[key])) result[key] = Math.max(min, Math.min(max, saved[key]));
  }
  if (CHAT_THEMES.some(theme => theme.id === saved?.chatTheme)) result.chatTheme = saved.chatTheme;
  if (['light', 'dark', 'system'].includes(saved?.mode)) result.mode = saved.mode;
  return result;
}
export function themeStyle(appearance, mode) {
  const theme = CHAT_THEMES.find(theme => theme.id === appearance.chatTheme) || CHAT_THEMES[0];
  const light = mode === 'light';
  const [incoming, outgoing, gradient, accent] = theme[light ? 'light' : 'dark'];
  const alpha = Math.round(appearance.bubbleOpacity * 2.55).toString(16).padStart(2, '0');
  return {
    '--theme-tint': light ? `${theme.tint}55` : theme.tint, '--accent': accent, '--gold': accent,
    '--accent-soft': `${accent}18`, '--bubble-in': `${incoming}${alpha}`, '--bubble-out': `${outgoing}${alpha}`,
    '--bubble-gradient': `${gradient}${alpha}`, '--chat-ink': light ? '#282630' : '#faf7fc',
    '--chat-muted': light ? '#635c6a' : '#c8c0ce', '--bubble-border': light ? '#2520351c' : '#ffffff1c',
    '--chat-accent': accent, '--bubble-radius': `${appearance.radius}px`, '--chat-font-size': `${appearance.fontSize}px`,
    '--composer-fill': incoming, '--chat-code': light ? '#eeeef4' : '#15141e',
  };
}
