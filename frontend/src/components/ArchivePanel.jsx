import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Panel, Pill, useLoad } from "@/components/kit";
import { API, call, short } from "@/lib/api";

export const ArchivePanel = () => {
  const [d, reload] = useLoad("/console/audit/archives");
  const [toSeq, setToSeq] = useState("");
  const [results, setResults] = useState({});
  const [upload, setUpload] = useState(null);
  const fileRef = useRef(null);
  const shaRef = useRef(null);

  const create = async () => { await call("post", "/console/audit/archives", toSeq ? { toSequence: Number(toSeq) } : {}, "Archive written (write-once)"); setToSeq(""); reload(); };
  const verify = async (id) => { const r = await call("post", `/console/audit/archives/${id}/verify`); if (r) setResults((x) => ({ ...x, [id]: r })); };
  const verifyFile = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const sumFile = shaRef.current?.files?.[0];
    const sha = sumFile ? (await sumFile.text()).trim() : undefined;
    const r = await call("post", "/console/audit/archives/verify-upload", { content: await file.text(), sha256: sha });
    setUpload(r);
    if (!r || !r.valid) { fileRef.current.value = ""; if (shaRef.current) shaRef.current.value = ""; }
  };

  return (
    <Panel title="Archive Export (NDJSON + SHA-256)" testId="archive-panel" className="mb-6"
      right={<Pill tone="info">{d?.unarchived ?? 0} unarchived</Pill>}>
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <Field label={`archive from #${d?.nextSequence ?? 1} up to sequence (blank = all)`}>
          <Input data-testid="archive-to-sequence-input" value={toSeq} onChange={(e) => setToSeq(e.target.value)} className="font-mono text-xs bg-[var(--surface)] w-56" />
        </Field>
        <Button data-testid="archive-create-button" onClick={create}>Export archive</Button>
      </div>
      <div className="divide-y divide-[var(--line)]">
        {(d?.archives || []).length === 0 && <div className="text-sm text-[var(--text-muted)] py-2">No archives yet. Exports are contiguous, write-once, and the live log is never deleted.</div>}
        {(d?.archives || []).map((a) => {
          const r = results[a.archive_id];
          return (
            <div key={a.archive_id} className="py-3" data-testid={`archive-row-${a.archive_id}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="font-mono text-sm text-[var(--text)]">{a.archive_id}.ndjson</div>
                  <div className="font-mono text-[11px] text-[var(--text-muted)]">#{a.from_sequence}–#{a.to_sequence} · {a.count} records · {(a.bytes / 1024).toFixed(1)} KB · sha256 {short(a.sha256, 16)}</div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <a data-testid={`archive-download-${a.archive_id}`} href={`${API}/console/audit/archives/${a.archive_id}/download`}><Button size="sm" variant="secondary">NDJSON</Button></a>
                  <a data-testid={`archive-checksum-${a.archive_id}`} href={`${API}/console/audit/archives/${a.archive_id}/checksum`}><Button size="sm" variant="secondary">.sha256</Button></a>
                  <Button data-testid={`archive-verify-${a.archive_id}`} size="sm" onClick={() => verify(a.archive_id)}>Re-verify</Button>
                </div>
              </div>
              {r && (
                <div className="mt-2 flex flex-wrap gap-2" data-testid={`archive-verify-result-${a.archive_id}`}>
                  <Pill tone={r.valid ? "ok" : "bad"}>{r.valid ? "verified" : "failed"}</Pill>
                  <Pill tone={r.checksumMatches ? "ok" : "bad"}>checksum {r.checksumMatches ? "match" : "mismatch"}</Pill>
                  <Pill tone={r.chain.valid ? "ok" : "bad"}>{r.chain.code}</Pill>
                  <Pill tone={r.matchesLiveLog ? "ok" : "bad"}>live log {r.matchesLiveLog ? "match" : "diverged"}</Pill>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-4 pt-4 border-t border-[var(--line)]">
        <div className="text-xs font-medium text-[var(--text-muted)] mb-2">verify a downloaded file</div>
        <div className="flex flex-wrap items-center gap-3">
          <input ref={fileRef} type="file" accept=".ndjson,.json,.txt" data-testid="archive-upload-file" className="text-xs text-[var(--text-2)] font-mono" />
          <input ref={shaRef} type="file" accept=".sha256,.txt" data-testid="archive-upload-sha" className="text-xs text-[var(--text-2)] font-mono" />
          <Button data-testid="archive-upload-verify-button" size="sm" variant="secondary" onClick={verifyFile}>Verify file</Button>
          {upload && <span data-testid="archive-upload-result"><Pill tone={upload.valid ? "ok" : "bad"}>{upload.valid ? "authentic" : upload.code}</Pill> <span className="font-mono text-[11px] text-[var(--text-muted)]">{upload.knownArchive || "no matching manifest"}</span></span>}
        </div>
      </div>
    </Panel>
  );
};
