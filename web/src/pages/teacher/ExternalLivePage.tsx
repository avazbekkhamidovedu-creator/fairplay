// Integrations: "Open FairPlay live room" in another system goes to /integrations/<source>/live, which sends the
// teacher here with a one-time code (/teacher/external-live?code=…). The code becomes a normal
// teacher sign-in, then the exam's live room opens. No password needed.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, contentApi, setToken } from "../../api/client";
import { Loading } from "../../components/Loading";

export const LIVE_LINK_EXPIRED = "This link has expired or was already used. Open the live room again from the page you came from.";

export default function ExternalLivePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const code = params.get("code") ?? "";
  const [error, setError] = useState<string | null>(code ? null : LIVE_LINK_EXPIRED);
  const started = useRef(false);

  useEffect(() => {
    if (!code || started.current) return;
    started.current = true; // the code works once (React may run effects twice in dev)
    contentApi.liveExchange(code).then(
      (res) => {
        setToken("teacher", res.token);
        navigate(`/teacher/exams/${encodeURIComponent(res.exam_id)}?tab=live`, { replace: true });
      },
      (e) => {
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) setError(LIVE_LINK_EXPIRED);
        else if (e instanceof ApiError) setError(e.detail);
        else setError("Cannot reach FairPlay. Check the connection, then open the live room again from the page you came from.");
      },
    );
  }, [code, navigate]);

  if (!error) return <Loading label="Opening the live room…" />;
  return (
    <main style={{ maxWidth: 520, margin: "64px auto", padding: "0 16px" }}>
      <div className="alert alert-error" role="alert" data-testid="external-live-error">
        {error}
      </div>
      <p className="t-small t-muted">
        Or <Link to="/teacher/login">sign in to FairPlay</Link> with your password.
      </p>
    </main>
  );
}
