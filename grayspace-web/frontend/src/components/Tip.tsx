/** Help for a label, shown on hover or focus: an ⓘ that keeps the explanation out of the page. */
import React from 'react';

export const Tip: React.FC<{ text: string }> = ({ text }) => (
  <span className="gs-tip" tabIndex={0} role="note" aria-label={text} data-tip={text}>ⓘ</span>
);
