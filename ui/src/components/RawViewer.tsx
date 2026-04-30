import { useState } from 'react';

interface Props {
  title: string;
  content: string | null | undefined;
}

export function RawViewer({ title, content }: Props) {
  const [open, setOpen] = useState(false);

  let display: string;
  if (content === null || content === undefined) {
    display = '(Raw content has been purged — session ended)';
  } else {
    try {
      display = JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      display = content;
    }
  }

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-800 hover:bg-gray-750 text-sm text-gray-300 font-medium"
      >
        <span>{title}</span>
        <span className="text-gray-500">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="bg-gray-900 p-4 overflow-auto max-h-96">
          <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-all">
            {display}
          </pre>
        </div>
      )}
    </div>
  );
}
