import React, { useEffect, useRef, useState } from 'react';
import { copyText } from '../utils/clipboard';

/**
 * A Copy button that says when it worked. When the browser won't copy, it shows the text selected in
 * a box instead, to copy with Ctrl+C, so the button never does nothing. In a row of buttons, give the
 * row flex-wrap: the box goes on a line of its own.
 */
export const CopyButton: React.FC<{ text: string | (() => string); label: string; className?: string }> = ({ text, label, className = 'gs-btn ghost sm' }) => {
  const [state, setState] = useState<'idle' | 'copied' | 'manual'>('idle');
  const [value, setValue] = useState('');
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (state === 'manual') box.current?.select();
  }, [state]);

  const copy = async () => {
    const found = typeof text === 'function' ? text() : text;
    setValue(found);
    setState((await copyText(found)) ? 'copied' : 'manual');
  };

  return (
    <>
      <button type="button" className={className} onClick={copy}>{state === 'copied' ? 'Copied' : label}</button>
      {state === 'manual' && (
        // On its own line after the buttons, in a wrapping row of them
        <div style={{ marginTop: 6, flexBasis: '100%', order: 99 }}>
          <div className="gs-muted" style={{ fontSize: 11 }}>This browser won't copy from here: press Ctrl+C (⌘C on a Mac) to copy it.</div>
          <textarea ref={box} readOnly value={value} aria-label="Text to copy" className="gs-mono" rows={Math.min(12, value.split('\n').length + 1)}
            style={{ width: '100%', fontSize: 11 }} onFocus={(e) => e.currentTarget.select()} />
        </div>
      )}
    </>
  );
};
