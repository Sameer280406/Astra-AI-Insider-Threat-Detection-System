import React from 'react';
import {Shield} from 'lucide-react';

export function initials(s: string) {
  return s.split(' ').map(x => x[0]).slice(0, 2).join('').toUpperCase();
}

export function time(s?: string) {
  if (!s) return '—';
  return new Date(s).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
}

export function scoreClass(n: number) {
  return n >= 85 ? 'critical' : n >= 65 ? 'high' : n >= 40 ? 'medium' : 'low';
}

export function Badge({children, tone = 'neutral'}: {children: React.ReactNode; tone?: string}) {
  return <span className={'badge ' + tone}>{children}</span>;
}

export function Score({value}: {value: number}) {
  const bounded = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <span className={'score ' + scoreClass(bounded)} title={`Risk score: ${bounded}/100`}>
      {bounded}
      <span className="score-track">
        <i style={{width: `${bounded}%`}} />
      </span>
    </span>
  );
}

export function Empty({
  title = 'Nothing here yet',
  text = 'New activity will appear here as events are processed.'
}: {
  title?: string;
  text?: string;
}) {
  return (
    <div className="empty">
      <Shield size={32} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}

export function Avatar({name, small = false}: {name: string; small?: boolean}) {
  return <span className={'avatar ' + (small ? 'small' : '')}>{initials(name)}</span>;
}

export function Panel({
  title,
  children,
  action,
  className = ''
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={'panel ' + className}>
      <div className="panel-heading">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function download(data: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <svg width="26" height="28" viewBox="0 0 30 30" aria-hidden="true">
          <path d="M15 2 29 27h-8L15 15 9 27H1Z" fill="currentColor" />
          <path d="M13 24h4v5h-4z" fill="currentColor" />
        </svg>
      </span>
      <span>
        astra<span className="brand-dot">.</span>
      </span>
      <Badge tone="low">INTELLIGENCE</Badge>
    </div>
  );
}

export function Field({
  name,
  value,
  onChange,
  type = 'text',
  placeholder = '',
  required = true
}: {
  name: string;
  value: any;
  onChange: (s: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
}) {
  return (
    <label>
      {name}
      <input
        required={required}
        type={type}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
      />
    </label>
  );
}

export function Metric({
  label,
  value,
  unit,
  icon,
  foot,
  tone = 'teal'
}: {
  label: string;
  value: any;
  unit?: string;
  icon: React.ReactNode;
  foot: string;
  tone?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-top">
        <span>{label}</span>
        <span className={'metric-icon ' + tone}>{icon}</span>
      </div>
      <div className="metric-value">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      <p>{foot}</p>
    </div>
  );
}
