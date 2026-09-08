import { useInvestigation } from '../InvestigationContext';

/**
 * What the model cost this investigation.
 *
 * Three figures, none of them recoverable from the answer itself:
 *
 *   requests   — how many round trips the run made. One planning call plus one
 *                per reasoning step, so a thin answer that made eight calls and
 *                a thin answer that made one mean opposite things.
 *   context    — the largest prompt sent, against the window it had. This is
 *                the number that decides whether the answer was written from
 *                the evidence or from the tail of a prompt that was silently
 *                cut; Ollama drops the *head* without erroring, so a run
 *                sitting near 100% is one evidence line from a confident
 *                answer that saw almost nothing.
 *   model      — which model actually served it. The backend switch and the
 *                provider setting are configured in different places, and
 *                "which model answered this" was previously only in a log line.
 *   cached     — how much of the prompt the provider served from its cache
 *                instead of re-reading. A run makes up to nine calls sharing one
 *                long identical prefix, so most of the prompt should be a cache
 *                read from the second call on. This is the only place that
 *                shows whether it actually is: a run paying full price nine
 *                times and a run paying once look identical in a total token
 *                count, and differ by roughly an order of magnitude in cost.
 */
export default function LlmUsage({ turn }) {
  const live = useInvestigation();
  const llmUsage = (turn || live).llmUsage;
  if (!llmUsage) return null;

  const {
    model, provider, requests, failed_requests: failedRequests,
    context_window: window, peak_prompt_tokens: peak, peak_context_used: used,
    prompt_tokens: promptTokens, output_tokens: outputTokens,
    cached_prompt_tokens: cachedTokens, cache_hit_ratio: cacheRatio,
    requests_by_stage: byStage, duration_ms: durationMs,
  } = llmUsage;

  const pct = used == null ? null : Math.round(used * 100);
  // Amber from 75%, red from 90%: past that a single extra observation is what
  // separates a complete prompt from a truncated one.
  const tone = pct == null ? '' : pct >= 90 ? 'li-llm--danger' : pct >= 75 ? 'li-llm--warn' : '';

  const stageBreakdown = Object.entries(byStage || {})
    .map(([stage, n]) => `${n} in ${stage}`).join(', ');

  return (
    <div className={`glass-panel li-llm ${tone}`}>
      <div className="li-llm-part">
        <span className="li-llm-label">LLM requests</span>
        <span className="li-llm-value">{requests ?? 0}</span>
        <span className="li-llm-sub">
          {stageBreakdown || 'no calls made'}
          {failedRequests > 0 && ` · ${failedRequests} failed`}
        </span>
      </div>

      <div className="li-llm-part">
        <span className="li-llm-label">Context window</span>
        {window ? (
          <>
            <span className="li-llm-value">
              {compact(peak)} / {compact(window)}
              {pct != null && <span className="li-llm-pct"> · {pct}%</span>}
            </span>
            <span className="li-llm-sub">
              largest prompt sent
              {pct != null && pct >= 90 && ' — close to truncation'}
            </span>
          </>
        ) : (
          <>
            <span className="li-llm-value">{compact(peak)} tokens</span>
            <span className="li-llm-sub">
              largest prompt sent · window not published by this provider
            </span>
          </>
        )}
      </div>

      {/* Shown only once there is something to show. A provider that does not
          report cache usage — Ollama, which caches locally and says nothing
          about it — would otherwise display a permanent, meaningless 0%. */}
      {cachedTokens > 0 && (
        <div className="li-llm-part">
          <span className="li-llm-label">Prompt cache</span>
          <span className="li-llm-value">
            {compact(cachedTokens)}
            {cacheRatio != null && (
              <span className="li-llm-pct"> · {Math.round(cacheRatio * 100)}%</span>
            )}
          </span>
          <span className="li-llm-sub">
            of the prompt served from cache instead of re-read
          </span>
        </div>
      )}

      <div className="li-llm-part li-llm-part--wide">
        <span className="li-llm-label">Model</span>
        <span className="li-llm-value li-llm-value--mono">{model || 'unknown'}</span>
        <span className="li-llm-sub">
          {provider || 'unknown provider'}
          {' · '}{compact((promptTokens || 0) + (outputTokens || 0))} tokens charged
          {durationMs ? ` · ${(durationMs / 1000).toFixed(1)}s in the model` : ''}
        </span>
      </div>
    </div>
  );
}

function compact(n) {
  if (!n && n !== 0) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
