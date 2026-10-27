import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { clearToken } from "../api/client";
import type { Role } from "../api/types";
import { useMe } from "../lib/useMe";
import { AppVersion } from "./AppVersion";
import { LogoMark, Wordmark } from "./ui/Icon";

export function Brand({ to }: { to?: string }) {
  const inner = (
    <>
      <LogoMark size={30} />
      <Wordmark />
    </>
  );
  return to ? (
    <Link className="brand" to={to}>{inner}</Link>
  ) : (
    <span className="brand">{inner}</span>
  );
}

export function AppHeader({ role, dark, children }: { role: Role; dark?: boolean; children?: ReactNode }) {
  const navigate = useNavigate();
  const me = useMe(role);
  const logout = () => {
    clearToken(role);
    navigate(`/${role}/login`);
  };
  return (
    <header className={`app-header${dark ? " dark" : ""}`}>
      <div className="app-header-inner">
        <Brand to={`/${role}`} />
        {children}
        <span className="header-spacer" />
        {me && (
          <div className="header-who">
            <span className="header-user">{me.name}</span>
            <AppVersion />
          </div>
        )}
        <button type="button" className="btn btn-secondary" onClick={logout}>
          Log out
        </button>
      </div>
    </header>
  );
}
