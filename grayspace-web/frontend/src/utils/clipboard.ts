/**
 * Copy text to the clipboard. The Clipboard API exists only in secure contexts (HTTPS or localhost),
 * and the container serves the UI over plain HTTP, so without it this falls back to a selected,
 * hidden textarea and execCommand('copy'), which browsers still allow from a click. False when
 * neither worked: show the text for the user to copy.
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Refused (permissions): try the old way
    }
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.top = '0';
  area.style.left = '-9999px';
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(area);
  }
}
