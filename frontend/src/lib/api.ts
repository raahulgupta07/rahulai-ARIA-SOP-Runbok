// API client for DocSensei backend.
// Auth = our JWT (Bearer). Page images are public (img tags can't carry headers).
const ORIGIN = import.meta.env.VITE_API ?? 'http://127.0.0.1:8077';
const BASE = `${ORIGIN}/api`;
const TOKEN = 'docsensei_token';

function token(): string { return localStorage.getItem(TOKEN) || ''; }

function headers(json = true): Record<string, string> {
  const h: Record<string, string> = {};
  if (json) h['Content-Type'] = 'application/json';
  const t = token();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

async function jsonOrThrow(r: Response) {
  if (r.status === 401) throw new Error('unauthorized');
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'request failed');
  return r.json();
}

export const api = {
  base: BASE,
  pageImg(pageId: number) { return `${BASE}/pages/${pageId}`; },

  async health() {
    const r = await fetch(`${BASE}/health`);
    return r.json();
  },

  // ---- version + notifications ----
  async version() {
    const r = await fetch(`${BASE}/version`);
    return r.json();
  },

  // public corpus stats for the login stat line (no auth)
  async publicStats() {
    try { const r = await fetch(`${BASE}/stats/public`); return r.ok ? await r.json() : null; }
    catch { return null; }
  },

  // curated bilingual hero starter chips (zero LLM at request; lang 'en' | 'my')
  async suggestions(lang: 'en' | 'my' = 'en') {
    try { const r = await fetch(`${BASE}/suggestions?lang=${lang}`, { headers: headers(false) }); return r.ok ? await r.json() : null; }
    catch { return null; }
  },
  // bandit reward — record a starter-chip click (fail-soft)
  chipClick(id: number, lang: 'en' | 'my' = 'en') {
    try { fetch(`${BASE}/suggestions/click`, { method: 'POST', headers: headers(), body: JSON.stringify({ id, lang }) }).catch(() => {}); } catch {}
  },

  // 👍/👎 (+ optional note) on an answer
  async feedback(body: { conversation_id: number | null; vote: 'up' | 'down'; text?: string; answer?: string }) {
    try { return await jsonOrThrow(await fetch(`${BASE}/feedback`, { method: 'POST', headers: headers(), body: JSON.stringify(body) })); }
    catch { return null; }
  },

  async notifications(filter = 'all') {
    return jsonOrThrow(await fetch(`${BASE}/notifications?filter=${filter}`, { headers: headers(false) }));
  },

  async markAllRead() {
    return jsonOrThrow(await fetch(`${BASE}/notifications/read-all`, { method: 'POST', headers: headers(false) }));
  },

  async ask(q: string, conversation_id?: number | null) {
    const r = await fetch(`${BASE}/ask`, {
      method: 'POST', headers: headers(), body: JSON.stringify({ q, conversation_id })
    });
    return jsonOrThrow(r);
  },

  // ---- streaming ask (NDJSON) ----
  // Streams the answer line-by-line. Each line is a JSON object:
  //   {type:'meta',conversation_id} | {type:'token',v} | {type:'done',pages,title,clean} | {type:'error',detail}
  async askStream(
    q: string,
    conversation_id: number | null,
    mode: string,
    onMeta: (obj: any) => void,
    onToken: (v: string) => void,
    onDone: (obj: any) => void,
    onError: (detail: string) => void,
    onStep?: (obj: any) => void,
    signal?: AbortSignal
  ) {
    let r: Response;
    try {
      r = await fetch(`${BASE}/ask/stream`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({ q, conversation_id, mode }),
        signal
      });
    } catch (e: any) {
      if (signal?.aborted) return;          // user pressed Stop — not an error
      onError(e?.message || 'network error');
      return;
    }
    if (!r.ok || !r.body) {
      if (r.status === 401) { onError('unauthorized'); return; }
      let detail = 'request failed';
      try { detail = (await r.json()).detail || detail; } catch {}
      onError(detail);
      return;
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const dispatch = (line: string) => {
      const s = line.trim();
      if (!s) return;
      let obj: any;
      try { obj = JSON.parse(s); } catch { return; }
      switch (obj.type) {
        case 'meta': onMeta(obj); break;
        case 'step': onStep?.(obj); break;
        case 'token': onToken(obj.v ?? ''); break;
        case 'done': onDone(obj); break;
        case 'error': onError(obj.detail || 'error'); break;
      }
    };
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl: number;
        // only parse complete lines (ending in \n); keep the remainder
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          dispatch(line);
        }
      }
      // flush any trailing complete object without a newline
      buffer += decoder.decode();
      if (buffer.trim()) dispatch(buffer);
    } catch (e: any) {
      if (signal?.aborted) return;          // aborted mid-stream — keep partial answer
      onError(e?.message || 'stream error');
    }
  },

  // ---- conversations (per-user chat history) ----
  async conversations() {
    return jsonOrThrow(await fetch(`${BASE}/conversations`, { headers: headers(false) }));
  },
  async newConversation() {
    return jsonOrThrow(await fetch(`${BASE}/conversations`, { method: 'POST', headers: headers(false) }));
  },
  async getConversation(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/conversations/${id}`, { headers: headers(false) }));
  },
  async renameConversation(id: number, title: string) {
    return jsonOrThrow(await fetch(`${BASE}/conversations/${id}`, { method: 'PATCH', headers: headers(), body: JSON.stringify({ title }) }));
  },
  async deleteConversation(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/conversations/${id}`, { method: 'DELETE', headers: headers(false) }));
  },

  async documents() {
    return jsonOrThrow(await fetch(`${BASE}/documents`, { headers: headers(false) }));
  },

  async upload(file: File) {
    const fd = new FormData();
    fd.append('file', file);
    const h: Record<string, string> = {};
    const t = token();
    if (t) h['Authorization'] = `Bearer ${t}`;
    return jsonOrThrow(await fetch(`${BASE}/upload`, { method: 'POST', headers: h, body: fd }));
  },

  async deleteDoc(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}`, { method: 'DELETE', headers: headers(false) }));
  },

  async retryDoc(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}/retry`, { method: 'POST', headers: headers(false) }));
  },
  async categorizeDoc(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}/categorize`, { method: 'POST', headers: headers(false) }));
  },
  async categorizeAll(force = false) {
    return jsonOrThrow(await fetch(`${BASE}/documents/categorize-all?force=${force}`, { method: 'POST', headers: headers(false) }));
  },
  async ingestState() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/state`, { headers: headers(false) }));
  },
  async ingestStop() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/stop`, { method: 'POST', headers: headers(false) }));
  },
  async ingestResume() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/resume`, { method: 'POST', headers: headers(false) }));
  },
  async cancelDoc(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}/cancel`, { method: 'POST', headers: headers(false) }));
  },
  async scanPreview() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/scan`, { headers: headers(false) }));
  },
  async ingestLog(after = 0, limit = 200) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/log?after=${after}&limit=${limit}`, { headers: headers(false) }));
  },
  async scanImport() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/scan`, { method: 'POST', headers: headers(false) }));
  },
  async s3ScanPreview() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/s3-scan`, { headers: headers(false) }));
  },
  async s3Import() {
    return jsonOrThrow(await fetch(`${BASE}/ingest/s3-import`, { method: 'POST', headers: headers(false) }));
  },
  // ---- Microsoft 365 credentials (Settings) ----
  async msConfig() {
    return jsonOrThrow(await fetch(`${BASE}/integrations/microsoft/config`, { headers: headers(false) }));
  },
  async msSaveConfig(cfg: Record<string, any>) {
    return jsonOrThrow(await fetch(`${BASE}/integrations/microsoft/config`, { method: 'POST', headers: headers(), body: JSON.stringify(cfg) }));
  },
  async msClearSecret() {
    return jsonOrThrow(await fetch(`${BASE}/integrations/microsoft/secret/clear`, { method: 'POST', headers: headers(false) }));
  },
  async msTest() {
    return jsonOrThrow(await fetch(`${BASE}/integrations/microsoft/test`, { method: 'POST', headers: headers(false) }));
  },
  // ---- SharePoint / OneDrive location + import ----
  async graphConfig(kind: string) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/graph/${kind}/config`, { headers: headers(false) }));
  },
  async graphSaveConfig(kind: string, cfg: Record<string, any>) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/graph/${kind}/config`, { method: 'POST', headers: headers(), body: JSON.stringify(cfg) }));
  },
  async graphTest(kind: string) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/graph/${kind}/test`, { method: 'POST', headers: headers(false) }));
  },
  async graphScan(kind: string) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/graph/${kind}/scan`, { headers: headers(false) }));
  },
  async graphImport(kind: string) {
    return jsonOrThrow(await fetch(`${BASE}/ingest/graph/${kind}/import`, { method: 'POST', headers: headers(false) }));
  },
  async storageConfig() {
    return jsonOrThrow(await fetch(`${BASE}/storage/config`, { headers: headers(false) }));
  },
  async storageSave(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/storage/config`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  async storageTest() {
    return jsonOrThrow(await fetch(`${BASE}/storage/test`, { method: 'POST', headers: headers(), body: '{}' }));
  },

  async docDetail(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}`, { headers: headers(false) }));
  },

  async docPages(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/pages`, { headers: headers(false) }));
  },

  async docText(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/text`, { headers: headers(false) }));
  },

  async pageText(pageId: number) {
    return jsonOrThrow(await fetch(`${BASE}/pages/${pageId}/text`, { headers: headers(false) }));
  },

  async pageMd(pageId: number) {
    return jsonOrThrow(await fetch(`${BASE}/pages/${pageId}/md`, { headers: headers(false) }));
  },

  async memory(status?: string) {
    const qs = status ? `?status=${status}` : '';
    return jsonOrThrow(await fetch(`${BASE}/memory${qs}`, { headers: headers(false) }));
  },

  async teach(value: string, k?: string) {
    return jsonOrThrow(await fetch(`${BASE}/memory`, {
      method: 'POST', headers: headers(), body: JSON.stringify({ value, key: k })
    }));
  },

  async deleteMemory(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/memory/${id}`, { method: 'DELETE', headers: headers(false) }));
  },

  async feedbackCorrections(limit = 30) {
    return jsonOrThrow(await fetch(`${BASE}/feedback/corrections?limit=${limit}`, { headers: headers(false) }));
  },

  async approveFact(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/memory/${id}/approve`, { method: 'POST', headers: headers(false) }));
  },

  async rejectFact(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/memory/${id}/reject`, { method: 'POST', headers: headers(false) }));
  },

  async getGovernance() {
    return jsonOrThrow(await fetch(`${BASE}/governance`, { headers: headers(false) }));
  },
  async saveGovernance(policy: any) {
    return jsonOrThrow(await fetch(`${BASE}/governance`, {
      method: 'POST', headers: headers(true), body: JSON.stringify(policy),
    }));
  },
  async approveBulk(opts: { ids?: number[]; source?: string }) {
    return jsonOrThrow(await fetch(`${BASE}/memory/approve-bulk`, {
      method: 'POST', headers: headers(true), body: JSON.stringify(opts),
    }));
  },

  async updateMemory(id: number, key: string | null, value: string) {
    return jsonOrThrow(await fetch(`${BASE}/memory/${id}`, {
      method: 'PATCH', headers: headers(true),
      body: JSON.stringify({ key, value }),
    }));
  },

  // ---- Q&A bank (auto-mined / chat-harvested question-answer pairs) ----
  async qa(status?: string) {
    const qs = status ? `?status=${status}` : '';
    return jsonOrThrow(await fetch(`${BASE}/qa${qs}`, { headers: headers(false) }));
  },
  async approveQa(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/qa/${id}/approve`, { method: 'POST', headers: headers(false) }));
  },
  async rejectQa(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/qa/${id}/reject`, { method: 'POST', headers: headers(false) }));
  },
  async deleteQa(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/qa/${id}`, { method: 'DELETE', headers: headers(false) }));
  },
  async updateQa(id: number, question: string | null, answer: string | null) {
    return jsonOrThrow(await fetch(`${BASE}/qa/${id}`, {
      method: 'PATCH', headers: headers(true),
      body: JSON.stringify({ question, answer }),
    }));
  },

  async auditCoverage(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/audit/coverage?days=${days}`, { headers: headers(false) }));
  },

  async digestLatest() {
    return jsonOrThrow(await fetch(`${BASE}/digest/latest`, { headers: headers(false) }));
  },

  async digestRun() {
    return jsonOrThrow(await fetch(`${BASE}/digest/run?force=true`, { method: 'POST', headers: headers(false) }));
  },

  async usage() {
    return jsonOrThrow(await fetch(`${BASE}/usage`, { headers: headers(false) }));
  },

  async dashboardMe(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/me?days=${days}`, { headers: headers(false) }));
  },

  async dashboardAdmin(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/admin?days=${days}`, { headers: headers(false) }));
  },

  async dashboardCockpit(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/cockpit?days=${days}`, { headers: headers(false) }));
  },

  async graphMe() {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/graph/me`, { headers: headers(false) }));
  },

  async graphPeople() {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/graph/people`, { headers: headers(false) }));
  },

  async dashboardKeywords(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/keywords?days=${days}`, { headers: headers(false) }));
  },

  async analyticsManagement(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/management?days=${days}`, { headers: headers(false) }));
  },
  async analyticsPerf(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/perf?days=${days}`, { headers: headers(false) }));
  },
  async analyticsDocs(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/docs?days=${days}`, { headers: headers(false) }));
  },
  async analyticsVerify(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/verify?days=${days}`, { headers: headers(false) }));
  },
  async verifyAnswer(messageId: number) {
    return jsonOrThrow(await fetch(`${BASE}/answer/verify/${messageId}`, { method: 'POST', headers: headers(false) }));
  },
  async lowAccuracy(threshold = 70, days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/low-accuracy?threshold=${threshold}&days=${days}`, { headers: headers(false) }));
  },
  async dashboardVitals() {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/vitals`, { headers: headers(false) }));
  },
  async activityHeatmap(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/dashboard/activity-heatmap?days=${days}`, { headers: headers(false) }));
  },
  async analyticsVerifyRun(limit = 10) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/verify/run?limit=${limit}`, { method: 'POST', headers: headers(false) }));
  },
  async ops() {
    return jsonOrThrow(await fetch(`${BASE}/ops`, { headers: headers(false) }));
  },
  async analyticsEngagement(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/engagement?days=${days}`, { headers: headers(false) }));
  },
  async analyticsLearningOverview(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/learning-overview?days=${days}`, { headers: headers(false) }));
  },
  async opsAsk(question: string, days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/ops/ask`, { method: 'POST', headers: headers(), body: JSON.stringify({ question, days }) }));
  },
  async shareAnswer(messageId: number) {
    return jsonOrThrow(await fetch(`${BASE}/messages/${messageId}/share`, { method: 'POST', headers: headers() }));
  },
  async getShare(token: string) {
    return jsonOrThrow(await fetch(`${BASE}/share/${token}`, { headers: headers(false) }));
  },
  async teamsConfig() {
    return jsonOrThrow(await fetch(`${BASE}/teams/config`, { headers: headers(false) }));
  },
  async teamsSaveConfig(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/teams/config`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  async teamsManifest() {
    return jsonOrThrow(await fetch(`${BASE}/teams/manifest`, { headers: headers(false) }));
  },
  async teamsManifestZip() {
    const r = await fetch(`${BASE}/teams/manifest.zip`, { headers: headers(false) });
    if (r.status === 401) throw new Error('unauthorized');
    if (!r.ok) throw new Error('request failed');
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'aria-teams.zip';
    a.click();
    URL.revokeObjectURL(a.href);
  },
  async analyticsLearning(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/learning?days=${days}`, { headers: headers(false) }));
  },
  async analyticsSecurity(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/security?days=${days}`, { headers: headers(false) }));
  },
  async analyticsCorpus() {
    return jsonOrThrow(await fetch(`${BASE}/analytics/corpus`, { headers: headers(false) }));
  },
  async adminAudit(p: { days?: number; action?: string; page?: number; page_size?: number } = {}) {
    const qs = new URLSearchParams(
      Object.entries({ days: 30, action: '', page: 1, page_size: 50, ...p }).map(([k, v]) => [k, String(v)])
    ).toString();
    return jsonOrThrow(await fetch(`${BASE}/admin/audit?${qs}`, { headers: headers(false) }));
  },
  async analyticsUsers(p: { days?: number; sort?: string; order?: string; q?: string; status?: string; role?: string; auth?: string; page?: number; page_size?: number } = {}) {
    const qs = new URLSearchParams(
      Object.entries({ days: 30, sort: 'questions', order: 'desc', q: '', status: 'all', role: '', auth: '', page: 1, page_size: 25, ...p })
        .map(([k, v]) => [k, String(v)])
    ).toString();
    return jsonOrThrow(await fetch(`${BASE}/analytics/users?${qs}`, { headers: headers(false) }));
  },
  async analyticsUsersExport(p: { days?: number; status?: string; role?: string; auth?: string } = {}) {
    const qs = new URLSearchParams(
      Object.entries({ days: 30, status: 'all', role: '', auth: '', ...p }).map(([k, v]) => [k, String(v)])
    ).toString();
    const r = await fetch(`${BASE}/analytics/users/export?${qs}`, { headers: headers(false) });
    return r.text();
  },
  async analyticsUserProfile(id: number, days = 90) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/users/${id}?days=${days}`, { headers: headers(false) }));
  },
  async analyticsConfig() {
    return jsonOrThrow(await fetch(`${BASE}/analytics/config`, { headers: headers(false) }));
  },
  async analyticsConfigSave(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/config`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },

  async brainGraph() {
    return jsonOrThrow(await fetch(`${BASE}/brain/graph?limit=400`, { headers: headers(false) }));
  },

  async answerRelated(pageIds: number[]) {
    if (!pageIds?.length) return { related: [] };
    return jsonOrThrow(await fetch(`${BASE}/answer/related?ids=${pageIds.join(',')}`, { headers: headers(false) }));
  },
  async answerGraph(pageIds: number[]) {
    if (!pageIds?.length) return { nodes: [], links: [] };
    return jsonOrThrow(await fetch(`${BASE}/answer/graph?ids=${pageIds.join(',')}`, { headers: headers(false) }));
  },

  // ---- admin ----
  async adminUsers() {
    return jsonOrThrow(await fetch(`${BASE}/admin/users`, { headers: headers(false) }));
  },
  async adminCreateUser(body: { email: string; name?: string; password: string; role: string }) {
    return jsonOrThrow(await fetch(`${BASE}/admin/users`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  async adminPatchUser(id: number, body: { role?: string; active?: boolean; name?: string }) {
    return jsonOrThrow(await fetch(`${BASE}/admin/users/${id}`, { method: 'PATCH', headers: headers(), body: JSON.stringify(body) }));
  },
  async adminDeleteUser(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/admin/users/${id}`, { method: 'DELETE', headers: headers(false) }));
  },
  async adminGetAuthConfig() {
    return jsonOrThrow(await fetch(`${BASE}/admin/auth-config`, { headers: headers(false) }));
  },
  async adminSaveAuthConfig(cfg: any) {
    return jsonOrThrow(await fetch(`${BASE}/admin/auth-config`, { method: 'PUT', headers: headers(), body: JSON.stringify(cfg) }));
  },
  async adminTestLdap(cfg: any) {
    return jsonOrThrow(await fetch(`${BASE}/admin/auth-config/test-ldap`, { method: 'POST', headers: headers(), body: JSON.stringify(cfg) }));
  },

  // ---- embeddable widget keys (admin) ----
  async embedKeys() {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys`, { headers: headers(false) }));
  },
  async embedCreateKey(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  async embedUpdateKey(id: number, body: any) {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys/${id}`, { method: 'PATCH', headers: headers(), body: JSON.stringify(body) }));
  },
  async embedRotateKey(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys/${id}/rotate`, { method: 'POST', headers: headers(false) }));
  },
  async embedDeleteKey(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys/${id}`, { method: 'DELETE', headers: headers(false) }));
  },
  async embedBrand() {
    return jsonOrThrow(await fetch(`${BASE}/embed/brand`, { headers: headers(false) }));
  },
  async embedSaveBrand(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/embed/brand`, { method: 'PUT', headers: headers(), body: JSON.stringify(body) }));
  },
  async embedStats(days = 30) {
    return jsonOrThrow(await fetch(`${BASE}/embed/stats?days=${days}`, { headers: headers(false) }));
  },
  async embedSandboxToken(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/embed/keys/${id}/sandbox-token`, { method: 'POST', headers: headers(false) }));
  },

  // ---- OKF export (Open Knowledge Format bundle) ----
  async okfPreview() {
    return jsonOrThrow(await fetch(`${BASE}/okf/preview`, { headers: headers(false) }));
  },
  async okfExport(images = false) {
    // authed blob download → trigger a save without leaving the page
    const r = await fetch(`${BASE}/okf/export${images ? '?images=1' : ''}`, { headers: headers(false) });
    if (!r.ok) throw new Error('export failed');
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'aria-knowledge-okf.tar.gz';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  },
  async okfImport(file: File, dryRun = false) {
    const fd = new FormData();
    fd.append('file', file);
    // headers(false) → no Content-Type, so the browser sets the multipart boundary
    return jsonOrThrow(await fetch(`${BASE}/okf/import?dry_run=${dryRun}`, {
      method: 'POST', headers: headers(false), body: fd }));
  },
  async okfImportUrl(url: string, dryRun = false) {
    const fd = new FormData();
    fd.append('url', url);
    return jsonOrThrow(await fetch(`${BASE}/okf/import?dry_run=${dryRun}`, {
      method: 'POST', headers: headers(false), body: fd }));
  }
};
