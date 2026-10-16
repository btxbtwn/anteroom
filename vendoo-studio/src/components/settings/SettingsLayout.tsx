import type { ReactNode } from "react";

export function SettingsSection({
  id,
  title,
  children,
}: {
  id: string;
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="settings-section" tabIndex={-1}>
      <h2 className="settings-section-title">{title}</h2>
      <div className="settings-group">{children}</div>
    </section>
  );
}

export function SettingsRow({
  id,
  title,
  description,
  status,
  control,
  children,
}: {
  id?: string;
  title: ReactNode;
  description?: ReactNode;
  status?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div id={id} className="settings-row" tabIndex={id ? -1 : undefined}>
      <div className="settings-row-main">
        <div className="settings-row-copy">
          <h3 className="settings-row-title">{title}</h3>
          {description ? <p className="settings-row-desc">{description}</p> : null}
          {status ? <div className="settings-row-status">{status}</div> : null}
        </div>
        {control ? <div className="settings-row-control">{control}</div> : null}
      </div>
      {children}
    </div>
  );
}
