declare class RFB {
  constructor(target: HTMLElement, urlOrChannel: string | WebSocket, options?: Record<string, unknown>);
  scaleViewport: boolean;
  resizeSession: boolean;
  viewOnly: boolean;
  focusOnClick: boolean;
  clipViewport: boolean;
  dragViewport: boolean;
  showDotCursor: boolean;
  background: string;
  qualityLevel: number;
  compressionLevel: number;
  disconnect(): void;
  sendCredentials(credentials: { password?: string; username?: string; target?: string }): void;
  sendKey(keysym: number, code: string | null, down?: boolean): void;
  sendCtrlAltDel(): void;
  focus(): void;
  blur(): void;
  machineShutdown(): void;
  machineReboot(): void;
  machineReset(): void;
  clipboardPasteFrom(text: string): void;
  addEventListener(event: string, handler: (e: CustomEvent) => void): void;
  removeEventListener(event: string, handler: (e: CustomEvent) => void): void;
}

interface NoVNCModule {
  default: typeof RFB;
}

declare var noVNC: NoVNCModule;
