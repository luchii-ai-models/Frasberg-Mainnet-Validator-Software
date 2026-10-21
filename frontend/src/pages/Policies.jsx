import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Field, Json, PageHeader, Panel, Pill, useLoad } from "@/components/kit";
import { call, short } from "@/lib/api";

const SAMPLE = {
  policyId: "frasberg-mainnet-policy-2026-09", version: "1.0.0", network: "frasberg-mainnet", epoch: 900,
  requirements: { validatorMin: "6.2.0", validatorRecommended: "6.4.1", validatorChannel: "stable", luchiiMin: "4.0.0" },
  publishedAt: "2026-09-28T00:00:00Z",
};

export default function Policies() {
  const [list, reload] = useLoad("/console/policies");
  const [text, setText] = useState(JSON.stringify(SAMPLE, null, 2));
  const [canon, setCanon] = useState(null);
  const [env, setEnv] = useState(null);
  const [verify, setVerify] = useState(null);
  const parse = () => { try { return JSON.parse(text); } catch { setCanon({ schemaErrors: ["Invalid JSON"] }); return null; } };

  const doCanon = async () => { const p = parse(); if (p) setCanon(await call("post", "/console/policies/canonicalize", { policy: p })); };
  const doSign = async () => {
    const p = parse(); if (!p) return;
    const r = await call("post", "/console/policies/sign", { policy: p }, "Policy signed (Ed25519)");
    if (r) { setEnv({ policy: r.policy, keyId: r.keyId, signature: r.signature }); reload(); }
  };
  const doVerify = async (e) => setVerify(await call("post", "/console/policies/verify", e));
  const tamper = () => env && setEnv({ ...env, policy: { ...env.policy, epoch: env.policy.epoch + 1 } });
  const flipSig = () => env && setEnv({ ...env, signature: (env.signature[0] === "A" ? "B" : "A") + env.signature.slice(1) });

  return (
    <div>
      <PageHeader kicker="Canonical JSON · Ed25519" title="Signed Policies" />
      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <Panel title="Policy Document" testId="policy-editor" right={<>
          <Button data-testid="policy-canonicalize-button" size="sm" variant="secondary" onClick={doCanon}>Canonicalize</Button>
          <Button data-testid="policy-sign-button" size="sm" onClick={doSign}>Sign</Button></>}>
          <Textarea data-testid="policy-json-input" value={text} onChange={(e) => setText(e.target.value)} rows={14} className="font-mono text-xs bg-[var(--surface)]" />
          {canon && (
            <div className="mt-4 space-y-2" data-testid="canonical-output">
              {canon.canonical && <Json value={canon.canonical} className="max-h-32" />}
              {canon.schemaErrors?.length ? canon.schemaErrors.map((e) => <div key={e} className="font-mono text-[11px] text-[var(--bad)]">✕ {e}</div>) : <Pill tone="ok">schema valid</Pill>}
            </div>
          )}
        </Panel>
        <Panel title="Signature Envelope" testId="signature-envelope" right={env && <>
          <Button data-testid="tamper-policy-button" size="sm" variant="secondary" onClick={tamper}>Tamper epoch</Button>
          <Button data-testid="tamper-signature-button" size="sm" variant="secondary" onClick={flipSig}>Flip sig byte</Button>
          <Button data-testid="verify-envelope-button" size="sm" onClick={() => doVerify(env)}>Verify</Button></>}>
          {env ? (
            <div className="space-y-3">
              <Field label="keyId (verification selects key by keyId)"><Input data-testid="envelope-keyid-input" value={env.keyId} onChange={(e) => setEnv({ ...env, keyId: e.target.value })} className="font-mono text-xs bg-[var(--surface)]" /></Field>
              <Json value={env} className="max-h-60" />
            </div>
          ) : <div className="text-sm text-[var(--text-muted)]">Sign a policy to produce an envelope, then tamper with it to watch verification fail.</div>}
          {verify && (
            <div className="mt-4 flex items-center gap-3" data-testid="verify-result">
              <Pill tone={verify.valid ? "ok" : "bad"}>{verify.valid ? "valid" : "invalid"}</Pill>
              <span className="font-mono text-sm">{verify.code}</span>
            </div>
          )}
        </Panel>
      </div>
      <Panel title="Published Policies" testId="policies-list">
        <div className="divide-y divide-[var(--line)]">
          {(list || []).map((p) => (
            <div key={p.signature} className="py-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-mono text-sm text-[var(--text)]">{p.policy.policyId}</div>
                <div className="font-mono text-[11px] text-[var(--text-muted)]">epoch {p.policy.epoch} · min {p.policy.requirements.validatorMin} · {p.signedAt}</div>
              </div>
              <div className="flex items-center gap-2">
                <Pill tone="violet">{p.keyId}</Pill>
                <span className="font-mono text-[11px] text-[var(--text-muted)]">{short(p.signature, 16)}</span>
                <Button data-testid={`verify-policy-${p.policy.epoch}`} size="sm" variant="secondary" onClick={() => { setEnv({ policy: p.policy, keyId: p.keyId, signature: p.signature }); doVerify(p); }}>Verify</Button>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
