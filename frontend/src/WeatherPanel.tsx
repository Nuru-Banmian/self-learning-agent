export type WeatherEvidence = {
  status?: string;
  date?: string | null;
  queried_at?: string;
  source_url?: string;
  attributions?: string[];
  location?: { name: string; id: string; country: string; adm1: string; adm2: string; lat: string; lon: string; tz: string } | null;
  candidates?: { id: string; name: string; country: string; adm1: string; adm2: string }[];
  forecast?: { temp_min: number; temp_max: number; day_text: string; night_text: string; precip_mm: number; precip_probability: number; wind_ms: number } | null;
  gaps?: string[];
  calls?: { tool: string; status: string; attempt: number; elapsed_ms?: number; http_status?: number }[];
};

const statuses: Record<string, string> = { running: "进行中", success: "成功", empty: "无结果", partial: "部分完成", error: "失败" };

export function WeatherPanel({ weather }: { weather: WeatherEvidence | null }) {
  if (!weather?.status) return null;
  const location = weather.location;
  const forecast = weather.forecast;
  return <section className="suggestions research" aria-label="天气与出行依据">
    <h2>天气与出行依据</h2>
    <p>执行 Agent · {statuses[weather.status] || weather.status}</p>
    {location && <p>{location.country} · {location.adm1} · {location.adm2} · {location.name}<br />
      经度 {location.lon} / 纬度 {location.lat} · 地点ID {location.id}<br />
      目的地时区：{location.tz}</p>}
    {weather.date && <p>适用日期：{weather.date}（目的地日期）</p>}
    {forecast ? <p>白天 {forecast.day_text} · 夜间 {forecast.night_text}<br />
      {forecast.temp_min}–{forecast.temp_max} °C · 预报降水 {forecast.precip_mm} mm<br />
      昼夜最高降水概率 {Math.round(forecast.precip_probability * 100)}% · 昼夜最高风速 {forecast.wind_ms} m/s</p>
      : <p>未取得对应日期的有效预报。</p>}
    {weather.gaps?.map((gap, i) => <p className="list-note" key={i}>{gap}</p>)}
    {!!weather.candidates?.length && <ul>{weather.candidates.map((candidate) => <li key={candidate.id}>
      {candidate.country} · {candidate.adm1} · {candidate.adm2} · {candidate.name} — 地点ID {candidate.id}
    </li>)}</ul>}
    <p>来源：<a href={weather.source_url} target="_blank" rel="noreferrer">和风天气</a><br />查询时间：{weather.queried_at}</p>
    {weather.attributions?.map((url, i) => <p key={i}><a href={url} target="_blank" rel="noreferrer">数据来源声明 {i + 1}</a></p>)}
    <details><summary>城市与天气实际调用 · {weather.calls?.length || 0} 次</summary>
      {weather.calls?.map((call, i) => <p key={i}>
        {call.tool === "qweather_city" ? "城市查询" : "逐日预报"} · {statuses[call.status] || call.status} · 第 {call.attempt} 次尝试 · {call.elapsed_ms ?? "…"} ms
        {call.http_status ? ` · HTTP ${call.http_status}` : ""}
      </p>)}
    </details>
    <p className="list-note">以上为外部天气事实。准备提示见聊天与行动建议，明确加入后才会成为待办。</p>
  </section>;
}
