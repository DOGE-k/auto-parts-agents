import { useEffect, useRef, useState } from "react";
import { parseStudyData, parseStudyNumber, type PpapStudyField, type StudyAppraiser,
  type StudyData, type StudyNumber } from "./ppapStudyData";

export type PpapStudyEditorProps = { field: PpapStudyField; value: string;
  onChange: (value: string) => void; disabled: boolean };
const FIELD_LABELS: Record<PpapStudyField, string> = { measurements: "MSA 原始测量数据",
  values: "过程原始样本序列", subgroups: "过程原始子组", photo_refs: "照片引用列表" };
const displayNumber = (value: StudyNumber) => value === null ? "" : String(value);

/** Local editing only: all saves, sources and authorization remain in the task form. */
export default function PpapStudyEditor({ field, value, onChange, disabled }: PpapStudyEditorProps) {
  const parsed = parseStudyData(field, value);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const lastEmitted = useRef<string | null>(null);
  const previousValue = useRef(value);
  const previousField = useRef(field);
  const editorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const fieldChanged = previousField.current !== field;
    const externallyLoaded = fieldChanged || (previousValue.current !== value && value !== lastEmitted.current);
    if (externallyLoaded) {
      // React can reuse input nodes when another record has the same shape. Native
      // customValidity survives node reuse, so reset it before applying new data.
      editorRef.current?.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input,textarea")
        .forEach((control) => control.setCustomValidity(""));
      setDrafts({});
      lastEmitted.current = null;
    }
    previousValue.current = value;
    previousField.current = field;
    editorRef.current?.querySelectorAll<HTMLInputElement>("input[aria-invalid]").forEach((input) =>
      input.setCustomValidity(!externallyLoaded && input.getAttribute("aria-invalid") === "true" ? "请填写有限数值，或留空表示缺测。" : ""));
    const rawEditor = editorRef.current?.querySelector<HTMLTextAreaElement>(".ppap-study-json textarea");
    rawEditor?.setCustomValidity(!parsed.supported && parsed.invalid ? parsed.error : "");
  }, [value, drafts, field]);

  const emit = (data: StudyData) => {
    if (disabled) return;
    const text = JSON.stringify(data, null, 2);
    lastEmitted.current = text;
    onChange(text);
  };
  const mutate = (change: (data: StudyData) => void, structural = false) => {
    if (disabled || !parsed.supported) return;
    // Invalid visible cells must be corrected before structural edits can shift their paths.
    if (structural && Object.keys(drafts).length) return;
    const data = JSON.parse(JSON.stringify(parsed.data)) as StudyData;
    change(data);
    emit(data);
  };
  const numericCell = (label: string, path: string, number: StudyNumber, write: (data: StudyData, next: StudyNumber) => void) =>
    <input type="text" inputMode="decimal" aria-label={label} disabled={disabled} placeholder="缺测"
      aria-invalid={path in drafts} value={drafts[path] ?? displayNumber(number)}
      onChange={(event) => {
        if (disabled) return;
        const text = event.target.value; const numberValue = parseStudyNumber(text);
        event.target.setCustomValidity(numberValue.valid ? "" : "请填写有限数值，或留空表示缺测；不能填写布尔值或非有限数值。");
        setDrafts((old) => { const next = { ...old }; if (numberValue.valid) delete next[path]; else next[path] = text; return next; });
        if (numberValue.valid) mutate((data) => write(data, numberValue.value));
      }} />;
  const structureDisabled = disabled || Object.keys(drafts).length > 0;
  const action = (label: string, change: (data: StudyData) => void) =>
    <button type="button" className="button secondary" disabled={structureDisabled}
      onClick={() => mutate(change, true)}>{label}</button>;

  let table = null;
  if (parsed.supported) {
    if (field === "measurements") {
      const data = parsed.data as StudyAppraiser[];
      const partCount = new Set(data.flatMap((appraiser) => appraiser.parts.map((part) => part.part ?? part.part_id ?? "").filter((part) => part.trim()))).size;
      const trialCounts = [...new Set(data.flatMap((appraiser) => appraiser.parts.map((part) => part.trials.length)))];
      table = <>
        <p className="muted">已识别 {data.length} 位评价人、{partCount} 个零件标识；测次{trialCounts.length ? `：${trialCounts.join(" / ")}` : "尚未设置"}。保持各记录原始顺序，不自动补齐缺测。</p>
        {!data.length && <p className="muted">尚无采集方案。请按实际研究新增评价人、零件与测次，再填写原始测量值。</p>}
        {data.map((appraiser, a) => {
          const trialCount = Math.max(0, ...appraiser.parts.map((part) => part.trials.length));
          return <fieldset key={a} className="ppap-study-appraiser"><legend>评价人 {a + 1}</legend>
            <label>评价人标识<input aria-label={`评价人 ${a + 1} 标识`} disabled={disabled} value={appraiser.appraiser}
              onChange={(event) => mutate((records) => { (records as StudyAppraiser[])[a].appraiser = event.target.value; })} /></label>
            <div className="ppap-actions">{action(`删除评价人 ${a + 1}`, (records) => { (records as StudyAppraiser[]).splice(a, 1); })}
              {action(`评价人 ${a + 1} 新增零件`, (records) => { (records as StudyAppraiser[])[a].parts.push({ part: "", trials: Array.from({ length: trialCount }, () => null) }); })}
              {appraiser.parts.length > 0 && action(`评价人 ${a + 1} 增加一轮测次`, (records) => { (records as StudyAppraiser[])[a].parts.forEach((part) => part.trials.push(null)); })}</div>
            {appraiser.parts.length > 0 && <div className="ppap-study-scroll"><table className="ppap-study-table"><thead><tr><th>零件标识</th>
              {Array.from({ length: trialCount }, (_, t) => <th key={t}>测次 {t + 1}</th>)}<th>调整采集</th></tr></thead><tbody>
              {appraiser.parts.map((part, p) => <tr key={p}><td><input aria-label={`评价人 ${a + 1} 零件 ${p + 1} 标识`} disabled={disabled} value={part.part ?? part.part_id ?? ""}
                onChange={(event) => mutate((records) => { const next = (records as StudyAppraiser[])[a].parts[p]; if ("part_id" in next) next.part_id = event.target.value; else next.part = event.target.value; })} /></td>
                {Array.from({ length: trialCount }, (_, t) => <td key={t}>{t < part.trials.length ? numericCell(`评价人 ${a + 1} 零件 ${p + 1} 测次 ${t + 1}`, `${a}/${p}/${t}`, part.trials[t],
                  (records, next) => { (records as StudyAppraiser[])[a].parts[p].trials[t] = next; }) : <span className="muted">未设置测次</span>}</td>)}
                <td>{action(`删除评价人 ${a + 1} 零件 ${p + 1}`, (records) => { (records as StudyAppraiser[])[a].parts.splice(p, 1); })}
                  {action(`评价人 ${a + 1} 零件 ${p + 1} 增加测次`, (records) => { (records as StudyAppraiser[])[a].parts[p].trials.push(null); })}
                  {part.trials.length > 0 && action(`评价人 ${a + 1} 零件 ${p + 1} 删除末次`, (records) => { (records as StudyAppraiser[])[a].parts[p].trials.pop(); })}</td></tr>)}
            </tbody></table></div>}
          </fieldset>;
        })}
        {action("新增评价人", (records) => { (records as StudyAppraiser[]).push({ appraiser: "", parts: [] }); })}
        <p className="muted">平均极差法当前支持 2–3 位评价人、2–10 个零件、2–3 次重复测量。采集维度由实际方案确定，表格不会代填结果。</p>
      </>;
    } else if (field === "values") {
      const data = parsed.data as StudyNumber[];
      table = <><p className="muted">已识别 {data.length} 个按录入顺序排列的原始样本。</p>
        <div className="ppap-study-scroll"><table className="ppap-study-table"><thead><tr><th>样本序号</th><th>原始实测值</th><th>调整采集</th></tr></thead><tbody>
          {data.map((number, index) => <tr key={index}><td>{index + 1}</td><td>{numericCell(`原始样本 ${index + 1}`, String(index), number,
            (records, next) => { (records as StudyNumber[])[index] = next; })}</td><td>{action(`删除样本 ${index + 1}`, (records) => { (records as StudyNumber[]).splice(index, 1); })}</td></tr>)}
        </tbody></table></div>{action("新增原始样本", (records) => { (records as StudyNumber[]).push(null); })}</>;
    } else if (field === "subgroups") {
      const data = parsed.data as StudyNumber[][];
      const size = Math.max(0, ...data.map((group) => group.length));
      table = <><p className="muted">已识别 {data.length} 个子组，组内数量：{data.length ? data.map((group) => group.length).join(" / ") : "尚未设置"}。子组边界与样本顺序保持不变。</p>
        <div className="ppap-study-scroll"><table className="ppap-study-table"><thead><tr><th>子组</th>
          {Array.from({ length: size }, (_, index) => <th key={index}>组内样本 {index + 1}</th>)}<th>调整采集</th></tr></thead><tbody>
          {data.map((group, g) => <tr key={g}><td>{g + 1}</td>{Array.from({ length: size }, (_, index) => <td key={index}>{index < group.length ?
            numericCell(`子组 ${g + 1} 样本 ${index + 1}`, `${g}/${index}`, group[index], (records, next) => { (records as StudyNumber[][])[g][index] = next; }) : <span className="muted">未设置样本</span>}</td>)}
            <td>{action(`删除子组 ${g + 1}`, (records) => { (records as StudyNumber[][]).splice(g, 1); })}
              {action(`子组 ${g + 1} 新增样本`, (records) => { (records as StudyNumber[][])[g].push(null); })}
              {group.length > 0 && action(`子组 ${g + 1} 删除末样本`, (records) => { (records as StudyNumber[][])[g].pop(); })}</td></tr>)}
        </tbody></table></div>{action("新增子组", (records) => { (records as StudyNumber[][]).push([]); })}</>;
    } else {
      const data = parsed.data as string[];
      table = <><p className="muted">每行一个照片引用，不上传文件，不改变引用顺序。</p>
        <textarea rows={4} aria-label="照片引用每行一个" disabled={disabled} value={data.join("\n")}
          onChange={(event) => emit(event.target.value === "" ? [] : event.target.value.split(/\r?\n/))} /></>;
    }
  }
  return <div className="ppap-study-editor" ref={editorRef}>
    {table}
    {!parsed.supported && <p role="alert" className="ppap-error">{parsed.error}</p>}
    {Object.keys(drafts).length > 0 && <p role="alert" className="ppap-error">存在未确认的数值。请修正标记的单元格后保存；原有数值未被覆盖。</p>}
    {field !== "photo_refs" && <p className="muted">空单元格保存为 null（缺测），不会按 0 计算。编号只表示原始录入顺序。</p>}
    <details className="ppap-study-json"><summary>高级 JSON · {FIELD_LABELS[field]}</summary>
      <textarea rows={5} aria-label={`${FIELD_LABELS[field]}高级 JSON`} disabled={disabled} value={value}
        onChange={(event) => {
          if (disabled) return;
          const next = event.target.value; const parsedNext = parseStudyData(field, next);
          event.target.setCustomValidity(!parsedNext.supported && parsedNext.invalid ? parsedNext.error : "");
          setDrafts({}); lastEmitted.current = next; onChange(next);
        }} />
      <p className="muted">用于核对原始结构。未识别的复杂结构只在这里修订，表格不会重塑或丢弃字段。</p>
    </details>
  </div>;
}
