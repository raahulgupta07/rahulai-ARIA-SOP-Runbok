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

  async documents(folderId?: number | null) {
    const qs = (folderId === undefined || folderId === null) ? '' : `?folder_id=${folderId}`;
    return jsonOrThrow(await fetch(`${BASE}/documents${qs}`, { headers: headers(false) }));
  },

  // ---- folders (document hub) ----
  async folders() {
    return jsonOrThrow(await fetch(`${BASE}/folders`, { headers: headers(false) }));
  },
  async createFolder(
    name: string,
    opts?: {
      access_mode?: 'sector' | 'specific' | 'org';
      principals?: { type: 'user' | 'group'; id: number }[];
      sector_id?: number | null;
      parent_id?: number | null;
    }
  ) {
    const body: Record<string, any> = { name };
    if (opts?.access_mode) body.access_mode = opts.access_mode;
    if (opts?.principals) body.principals = opts.principals;
    if (opts?.sector_id !== undefined) body.sector_id = opts.sector_id;
    if (opts?.parent_id !== undefined) body.parent_id = opts.parent_id;
    return jsonOrThrow(await fetch(`${BASE}/folders`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  // get-or-create a folder under a parent (idempotent); returns the folder row + `created`.
  async ensureFolder(name: string, parentId: number | null) {
    return this.createFolder(name, { parent_id: parentId });
  },
  // rename / move / persist expand state
  async patchFolder(
    id: number,
    opts: { name?: string; parent_id?: number | null; move?: boolean; is_expanded?: boolean }
  ) {
    return jsonOrThrow(await fetch(`${BASE}/folders/${id}`, {
      method: 'PATCH', headers: headers(), body: JSON.stringify(opts)
    }));
  },

  // current access for a folder (preloads the Share modal)
  async folderAccess(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/folders/${id}/access`, { headers: headers(false) }));
  },
  // save access back from the Share modal
  async setFolderAccess(
    id: number,
    opts: {
      access_mode: 'sector' | 'specific' | 'org';
      principals: { type: 'user' | 'group'; id: number }[];
    }
  ) {
    return jsonOrThrow(await fetch(`${BASE}/folders/${id}/access`, {
      method: 'PUT',
      headers: headers(),
      body: JSON.stringify({ access_mode: opts.access_mode, principals: opts.principals })
    }));
  },
  // delete a folder (RBAC-gated server-side); its docs are un-filed, not deleted
  async deleteFolder(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/folders/${id}`, { method: 'DELETE', headers: headers() }));
  },

  // admin: users + groups to populate the folder-access picker
  async principals() {
    return jsonOrThrow(await fetch(`${BASE}/principals`, { headers: headers(false) }));
  },

  async upload(file: File) {
    const fd = new FormData();
    fd.append('file', file);
    const h: Record<string, string> = {};
    const t = token();
    if (t) h['Authorization'] = `Bearer ${t}`;
    return jsonOrThrow(await fetch(`${BASE}/upload`, { method: 'POST', headers: h, body: fd }));
  },

  // like upload() but routes the file into a folder when given
  async uploadTo(file: File, folderId?: number | null) {
    const fd = new FormData();
    fd.append('file', file);
    if (folderId !== undefined && folderId !== null) fd.append('folder_id', String(folderId));
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
  async docProcessing(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}/processing`, { headers: headers(false) }));
  },
  async moveDoc(id: number, folderId: number | null) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}`, {
      method: 'PATCH', headers: headers(true), body: JSON.stringify({ folder_id: folderId }),
    }));
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
  // ---- Enrichment Agent (deferred phase-2 lane) ----
  async enrichStatus() {
    return jsonOrThrow(await fetch(`${BASE}/enrich/status`, { headers: headers(false) }));
  },
  async enrichPause() {
    return jsonOrThrow(await fetch(`${BASE}/enrich/pause`, { method: 'POST', headers: headers(false) }));
  },
  async enrichResume() {
    return jsonOrThrow(await fetch(`${BASE}/enrich/resume`, { method: 'POST', headers: headers(false) }));
  },
  async enrichConcurrency(concurrency: number) {
    return jsonOrThrow(await fetch(`${BASE}/enrich/concurrency`, { method: 'POST', headers: headers(), body: JSON.stringify({ concurrency }) }));
  },
  async enrichSkip(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/enrich/doc/${docId}/skip`, { method: 'POST', headers: headers(false) }));
  },
  // ---- Eval Agent (offline answer-quality scoring) ----
  async evalStatus() {
    return jsonOrThrow(await fetch(`${BASE}/eval/status`, { headers: headers(false) }));
  },
  async evalDocs() {
    return jsonOrThrow(await fetch(`${BASE}/eval/docs`, { headers: headers(false) }));
  },
  async evalDoc(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/eval/doc/${docId}`, { headers: headers(false) }));
  },
  async evalRun() {
    return jsonOrThrow(await fetch(`${BASE}/eval/run`, { method: 'POST', headers: headers(false) }));
  },
  async evalPause() {
    return jsonOrThrow(await fetch(`${BASE}/eval/pause`, { method: 'POST', headers: headers(false) }));
  },
  async evalResume() {
    return jsonOrThrow(await fetch(`${BASE}/eval/resume`, { method: 'POST', headers: headers(false) }));
  },
  async evalConcurrency(concurrency: number) {
    return jsonOrThrow(await fetch(`${BASE}/eval/concurrency`, { method: 'POST', headers: headers(), body: JSON.stringify({ concurrency }) }));
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
  // ---- unified SharePoint connector (push / device / app) ----
  async spGet() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp`, { headers: headers(false) }));
  },
  async spSave(body: any) {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp`, { method: 'POST', headers: headers(), body: JSON.stringify(body) }));
  },
  async spStatus() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/status`, { headers: headers(false) }));
  },
  async spSyncNow() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/sync-now`, { method: 'POST', headers: headers(false) }));
  },
  async spRotateToken() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/token/rotate`, { method: 'POST', headers: headers(false) }));
  },
  async spAgentScript(os: 'win' | 'mac' = 'mac') {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/agent-script?os=${os}`, { headers: headers(false) }));
  },
  async spDeviceStart() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/device/start`, { method: 'POST', headers: headers(false) }));
  },
  async spDevicePoll(deviceCode = '') {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/device/poll`, { method: 'POST', headers: headers(), body: JSON.stringify({ device_code: deviceCode }) }));
  },
  async spDeviceDisconnect() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/device/disconnect`, { method: 'POST', headers: headers(false) }));
  },
  async spAppTest() {
    return jsonOrThrow(await fetch(`${BASE}/connector/sp/app/test`, { method: 'POST', headers: headers(false) }));
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

  async getPlaybook(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/playbook`, { headers: headers(false) }));
  },

  async getLookup(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/lookup`, { headers: headers(false) }));
  },

  async getTree(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/tree`, { headers: headers(false) }));
  },

  async getPageCues(pageId: number) {
    return jsonOrThrow(await fetch(`${BASE}/pages/${pageId}/cues`, { headers: headers(false) }));
  },

  async getDependencies(docId: number) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${docId}/dependencies`, { headers: headers(false) }));
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

  async getFeatures() {
    return jsonOrThrow(await fetch(`${BASE}/settings/features`, { headers: headers(false) }));
  },
  async saveFeatures(patch: Record<string, boolean>) {
    return jsonOrThrow(await fetch(`${BASE}/settings/features`, {
      method: 'POST', headers: headers(true), body: JSON.stringify(patch),
    }));
  },
  // ---- Persona (agent identity / voice, generated from documents) ----
  async getPersona() {
    return jsonOrThrow(await fetch(`${BASE}/settings/persona`, { headers: headers(false) }));
  },
  async savePersona(patch: any) {
    return jsonOrThrow(await fetch(`${BASE}/settings/persona`, {
      method: 'POST', headers: headers(true), body: JSON.stringify(patch),
    }));
  },
  async generatePersona() {
    return jsonOrThrow(await fetch(`${BASE}/settings/persona/generate`, {
      method: 'POST', headers: headers(true),
    }));
  },
  async personaHistory() {
    return jsonOrThrow(await fetch(`${BASE}/settings/persona/history`, { headers: headers(false) }));
  },
  async getWikiSchema() {
    return jsonOrThrow(await fetch(`${BASE}/settings/wiki-schema`, { headers: headers(false) }));
  },
  async saveWikiSchema(patch: any) {
    return jsonOrThrow(await fetch(`${BASE}/settings/wiki-schema`, {
      method: 'POST', headers: headers(true), body: JSON.stringify(patch),
    }));
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

  async dreamLatest() {
    return jsonOrThrow(await fetch(`${BASE}/dream/latest`, { headers: headers(false) }));
  },
  async dreamRuns(limit = 10) {
    return jsonOrThrow(await fetch(`${BASE}/dream/runs?limit=${limit}`, { headers: headers(false) }));
  },
  async dreamRun() {
    return jsonOrThrow(await fetch(`${BASE}/dream/run?force=true`, { method: 'POST', headers: headers(false) }));
  },
  async dreamConfig() {
    return jsonOrThrow(await fetch(`${BASE}/dream/config`, { headers: headers(false) }));
  },
  async dreamSaveConfig(patch: any) {
    return jsonOrThrow(await fetch(`${BASE}/dream/config`, { method: 'POST', headers: headers(), body: JSON.stringify(patch) }));
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
  async docEval() {
    return jsonOrThrow(await fetch(`${BASE}/analytics/doc-eval`, { headers: headers(false) }));
  },
  async docEvalRun(maxQ = 6) {
    return jsonOrThrow(await fetch(`${BASE}/analytics/doc-eval/run?max_q=${maxQ}`, { method: 'POST', headers: headers(false) }));
  },
  // ---- Self-Heal agent (per-doc grounded-answer healing loop) ----
  async selfheal() {
    return jsonOrThrow(await fetch(`${BASE}/analytics/selfheal`, { headers: headers(false) }));
  },
  async selfhealLogs(docId?: number | null, after = 0) {
    const qs = `?${docId != null ? `doc_id=${docId}&` : ''}after=${after}`;
    return jsonOrThrow(await fetch(`${BASE}/analytics/selfheal/logs${qs}`, { headers: headers(false) }));
  },
  async selfhealRun(docId?: number | null) {
    const qs = docId != null ? `?doc_id=${docId}` : '';
    return jsonOrThrow(await fetch(`${BASE}/analytics/selfheal/run${qs}`, { method: 'POST', headers: headers(false) }));
  },
  // ---- GraphRAG explorer (entity graph + path + global community queries) ----
  async graphragGraph(limit = 300) {
    return jsonOrThrow(await fetch(`${BASE}/graphrag/graph?limit=${limit}`, { headers: headers(false) }));
  },
  async graphragEntity(id?: number | null, name?: string | null) {
    const qs = id != null ? `id=${id}` : `name=${encodeURIComponent(name || '')}`;
    return jsonOrThrow(await fetch(`${BASE}/graphrag/entity?${qs}`, { headers: headers(false) }));
  },
  async graphragPath(a: string, b: string) {
    const qs = `a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`;
    return jsonOrThrow(await fetch(`${BASE}/graphrag/path?${qs}`, { headers: headers(false) }));
  },
  async graphragGlobal(q: string) {
    return jsonOrThrow(await fetch(`${BASE}/graphrag/global?q=${encodeURIComponent(q)}`, { headers: headers(false) }));
  },
  async graphragStats() {
    return jsonOrThrow(await fetch(`${BASE}/graphrag/stats`, { headers: headers(false) }));
  },
  async graphragBuildCommunities() {
    return jsonOrThrow(await fetch(`${BASE}/graphrag/communities/build`, { method: 'POST', headers: headers(false) }));
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
  // ---- Operations Cockpit (live answer feed + per-doc ingest log) ----
  async opsAnswersRecent(limit = 20) {
    return jsonOrThrow(await fetch(`${BASE}/ops/answers/recent?limit=${limit}`, { headers: headers(false) }));
  },
  async documentLog(id: number, limit = 200) {
    return jsonOrThrow(await fetch(`${BASE}/documents/${id}/log?limit=${limit}`, { headers: headers(false) }));
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
  async answerKg(pageIds: number[]) {
    if (!pageIds?.length) return { nodes: [], edges: [] };
    return jsonOrThrow(await fetch(`${BASE}/answer/kg?ids=${pageIds.join(',')}`, { headers: headers(false) }));
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

  // ---- sector RBAC: sectors / user roles / groups (super-admin) ----
  async adminSectors() {
    return jsonOrThrow(await fetch(`${BASE}/admin/sectors`, { headers: headers(false) }));
  },
  async adminCreateSector(name: string, label?: string) {
    return jsonOrThrow(await fetch(`${BASE}/admin/sectors`, { method: 'POST', headers: headers(), body: JSON.stringify({ name, label }) }));
  },
  async adminDeleteSector(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/admin/sectors/${id}`, { method: 'DELETE', headers: headers(false) }));
  },
  // multi-tenant RBAC switch (super-admin)
  async adminRbac() {
    return jsonOrThrow(await fetch(`${BASE}/admin/rbac`, { headers: headers(false) }));
  },
  async adminSetRbac(enabled: boolean) {
    return jsonOrThrow(await fetch(`${BASE}/admin/rbac`, { method: 'POST', headers: headers(), body: JSON.stringify({ enabled }) }));
  },
  async adminSetUser(id: number, body: { role?: string; sector_id?: number | null }) {
    return jsonOrThrow(await fetch(`${BASE}/admin/users/${id}`, { method: 'PATCH', headers: headers(), body: JSON.stringify(body) }));
  },
  async adminGroups() {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups`, { headers: headers(false) }));
  },
  async adminCreateGroup(name: string, allSectors = false) {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups`, { method: 'POST', headers: headers(), body: JSON.stringify({ name, all_sectors: allSectors }) }));
  },
  async adminDeleteGroup(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups/${id}`, { method: 'DELETE', headers: headers(false) }));
  },
  async adminSetGroupFeatures(gid: number, features: string[]) {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups/${gid}/features`, { method: 'PUT', headers: headers(), body: JSON.stringify({ features }) }));
  },
  async adminAddMember(gid: number, uid: number) {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups/${gid}/members/${uid}`, { method: 'POST', headers: headers(false) }));
  },
  async adminRemoveMember(gid: number, uid: number) {
    return jsonOrThrow(await fetch(`${BASE}/admin/groups/${gid}/members/${uid}`, { method: 'DELETE', headers: headers(false) }));
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
  },

  // ---- entities (cross-doc index: code/menu_path/system/screen/field/term) ----
  async entities(q = '', limit = 200) {
    return jsonOrThrow(await fetch(`${BASE}/entities?q=${encodeURIComponent(q)}&limit=${limit}`, { headers: headers(false) }));
  },
  async entityDetail(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/entities/${id}`, { headers: headers(false) }));
  },

  // ---- capabilities (master catalog: everything Aria can help with, grouped) ----
  async getCatalog() {
    return jsonOrThrow(await fetch(`${BASE}/catalog`, { headers: headers(false) }));
  },

  // ---- white-label branding (one-logo: name/mark/favicon/icons + accent generated) ----
  async brand() {
    return jsonOrThrow(await fetch(`${BASE}/brand`, { headers: headers(false) }));
  },
  async saveBrand(fd: FormData) {
    // headers(false) → no Content-Type so the browser sets the multipart boundary
    return jsonOrThrow(await fetch(`${BASE}/admin/brand`, { method: 'POST', headers: headers(false), body: fd }));
  },

  // ---- KB health (conflicts + stale docs) ----
  async kbConflicts(status = 'open') {
    return jsonOrThrow(await fetch(`${BASE}/kb/conflicts?status=${status}`, { headers: headers(false) }));
  },
  async kbLint() {
    return jsonOrThrow(await fetch(`${BASE}/kb/lint`, { method: 'POST', headers: headers(false) }));
  },
  async kbDismissConflict(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/kb/conflicts/${id}/dismiss`, { method: 'POST', headers: headers(false) }));
  },
  async kbStale(days = 90) {
    return jsonOrThrow(await fetch(`${BASE}/kb/stale?days=${days}`, { headers: headers(false) }));
  },

  // ---- contradictions review queue (Karpathy "LLM wiki" Phase 1) ----
  async getContradictions(status = 'pending') {
    return jsonOrThrow(await fetch(`${BASE}/contradictions?status=${status}`, { headers: headers(false) }));
  },
  async resolveContradiction(id: number, choice: 'new' | 'old' | 'both' | 'dismiss') {
    return jsonOrThrow(await fetch(`${BASE}/contradictions/${id}/resolve`, {
      method: 'POST', headers: headers(true), body: JSON.stringify({ choice }),
    }));
  },

  // ---- Wiki (Karpathy "LLM wiki" Phase 3 — browsable entity/doc knowledge base) ----
  async wikiIndex() {
    return jsonOrThrow(await fetch(`${BASE}/wiki/index`, { headers: headers(false) }));
  },
  async wikiEntity(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/wiki/entity/${id}`, { headers: headers(false) }));
  },
  async wikiResolve(name: string) {
    const r = await fetch(`${BASE}/wiki/resolve?name=${encodeURIComponent(name)}`, { headers: headers(false) });
    return r.ok ? r.json() : null;
  },
  async wikiDoc(id: number) {
    return jsonOrThrow(await fetch(`${BASE}/wiki/doc/${id}`, { headers: headers(false) }));
  }
};
