import { useEffect, useState } from 'react'
import { engineeringApi, type ModelUsage, type UsageWindow } from './api'

// What the models have been asked to do, beside the build that is asking.
//
// Spending is measured: every call records its tokens when it returns. What is
// LEFT is only shown when a limit is configured, because no provider we use
// reports a remaining quota -- Gemini answers 429 when it is gone, `agy`
// refuses when the subscription is spent. A percentage against an invented
// ceiling would be the one number on this page nobody could check.
//
// The 429 count is the exception: that is the quota itself answering, recorded
// when it happened, so it is shown whether or not a limit is set.

function compact(value: number): string {
  if (value < 1000) return String(value)
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`
  return `${(value / 1_000_000).toFixed(1)}M`
}

function Window({ label, window: data }: { label: string; window: UsageWindow }) {
  const short = !data.measured
  return (
    <span className="usage-window" data-spent={data.percent_used != null
      && data.percent_used >= 90}>
      <span className="usage-label">{label}</span>
      {short ? (
        <span className="usage-value quiet" title="No calls have been recorded by hour yet.
The build process records them from its next start.">not recorded yet</span>
      ) : (
        <span className="usage-value">
          {data.limit == null
            ? `${compact(data.calls)} ${data.calls === 1 ? 'call' : 'calls'}`
            : `${compact(data.calls)}/${compact(data.limit)}`}
          {data.tokens > 0 && (
            <em className="usage-tokens"> · {compact(data.tokens)} tok</em>
          )}
        </span>
      )}
    </span>
  )
}

export function UsageStrip() {
  const [usage, setUsage] = useState<ModelUsage | null>(null)

  useEffect(() => {
    const ask = () => engineeringApi.usage().then(setUsage).catch(() => setUsage(null))
    void ask()
    // A minute: this is a running total, not a live wire, and asking faster
    // would spend a request to watch a number that changes per model call.
    const timer = setInterval(ask, 60_000)
    return () => clearInterval(timer)
  }, [])

  // A response from an older service, or one that lost a field on the way, must
  // not take the workspace down with it: a usage strip is the least important
  // thing on this page and it sits inside the command bar of the most
  // important one.
  if (!usage?.hour || !usage?.week) return null

  const limited = usage.week.rate_limited
  return (
    <div className="usage-strip" data-testid="usage-strip"
      title={usage.limits_configured
        ? 'Calls against the limits set in configuration.'
        : 'No plan limit is configured, so this is what was spent, not what is left. '
          + 'Set USAGE_HOURLY_LIMIT and USAGE_WEEKLY_LIMIT to see what remains.'}>
      <span className="micro-label">Models</span>
      <Window label="1h" window={usage.hour} />
      <Window label="7d" window={usage.week} />
      {limited > 0 && (
        <span className="usage-limited" title="Times a provider answered 429 in the last
seven days. This is the only limit figure a provider actually reported.">
          {limited} rate limited
        </span>
      )}
      {!usage.limits_configured && (
        <span className="usage-note quiet">no limit set</span>
      )}
    </div>
  )
}
