'use client';

import { useState, useCallback } from 'react';
import { getSupabase } from '@/lib/supabase';
import { cn } from '@/lib/utils';

interface Props {
  onSaved: () => void;
}

const CHANGE_TYPES = [
  { value: 'gpfc', label: 'GPFC' },
  { value: 'param_change', label: 'Param Change' },
  { value: 'bugfix', label: 'Bug Fix' },
  { value: 'feature', label: 'Feature' },
  { value: 'strategy', label: 'Strategy' },
  { value: 'revert', label: 'Revert' },
];

export function ChangelogForm({ onSaved }: Props) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const [title, setTitle] = useState('');
  const [changeType, setChangeType] = useState('gpfc');
  const [version, setVersion] = useState('');
  const [description, setDescription] = useState('');
  const [paramsBefore, setParamsBefore] = useState('');
  const [paramsAfter, setParamsAfter] = useState('');

  const handleSave = useCallback(async () => {
    if (!title.trim()) { setError('Title is required'); return; }
    setError('');
    setSaving(true);

    try {
      // Validate JSON if provided
      let beforeJson = null;
      let afterJson = null;
      if (paramsBefore.trim()) {
        try { beforeJson = JSON.parse(paramsBefore); } catch { setError('Invalid JSON in "Before" params'); setSaving(false); return; }
      }
      if (paramsAfter.trim()) {
        try { afterJson = JSON.parse(paramsAfter); } catch { setError('Invalid JSON in "After" params'); setSaving(false); return; }
      }

      const sb = getSupabase();
      if (!sb) throw new Error('No connection');

      const { error: dbErr } = await sb.from('changelog').insert({
        title: title.trim(),
        change_type: changeType,
        version: version.trim() || null,
        description: description.trim() || null,
        parameters_before: beforeJson,
        parameters_after: afterJson,
        status: 'deployed',
        deployed_at: new Date().toISOString(),
      });

      if (dbErr) throw dbErr;

      // Reset form
      setTitle('');
      setVersion('');
      setDescription('');
      setParamsBefore('');
      setParamsAfter('');
      setOpen(false);
      onSaved();
    } catch (err: any) {
      setError(err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  }, [title, changeType, version, description, paramsBefore, paramsAfter, onSaved]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-zinc-300 hover:text-white hover:bg-zinc-800/30 transition-colors"
      >
        <span>+ Add Changelog Entry</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
          className={cn('transition-transform', open ? 'rotate-180' : '')}>
          <path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-zinc-800/50">
          {error && (
            <div className="rounded-lg bg-red-400/10 border border-red-400/20 px-3 py-2 text-xs text-red-400">
              {error}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-3">
            <div>
              <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Title *</label>
              <input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="GPFC #18: Smart Cooldown"
                className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-xs text-white placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Type</label>
              <select
                value={changeType}
                onChange={e => setChangeType(e.target.value)}
                className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-xs text-white focus:border-blue-500 focus:outline-none"
              >
                {CHANGE_TYPES.map(ct => (
                  <option key={ct.value} value={ct.value}>{ct.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Version</label>
              <input
                value={version}
                onChange={e => setVersion(e.target.value)}
                placeholder="v3.22.9"
                className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-xs text-white placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="What changed and why..."
              rows={3}
              className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-xs text-white placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none resize-none"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Params Before (JSON)</label>
              <textarea
                value={paramsBefore}
                onChange={e => setParamsBefore(e.target.value)}
                placeholder='{"SL_COOLDOWN_SECONDS": 120}'
                rows={3}
                className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-[11px] font-mono text-white placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none resize-none"
              />
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1 block">Params After (JSON)</label>
              <textarea
                value={paramsAfter}
                onChange={e => setParamsAfter(e.target.value)}
                placeholder='{"SL_COOLDOWN_SECONDS": 30}'
                rows={3}
                className="w-full rounded-lg bg-zinc-900 border border-zinc-700 px-3 py-2 text-[11px] font-mono text-white placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none resize-none"
              />
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={saving}
              className="rounded-lg bg-blue-500/20 border border-blue-500/40 px-4 py-2 text-xs font-bold text-blue-400 hover:bg-blue-500/30 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save Entry'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
