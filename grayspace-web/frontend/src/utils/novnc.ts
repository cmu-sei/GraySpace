/**
 * noVNC (public/novnc.js, a prebuilt bundle that sets the global noVNC), loaded the first time a
 * desktop opens rather than in index.html.
 */
let loading: Promise<NoVNCModule> | null = null;

export function loadNoVNC(): Promise<NoVNCModule> {
  if (typeof noVNC !== 'undefined') return Promise.resolve(noVNC);
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `${process.env.PUBLIC_URL}/novnc.js`;
    script.onload = () => resolve(noVNC);
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new Error("Couldn't load the desktop viewer"));
    };
    document.head.appendChild(script);
  });
  return loading;
}
