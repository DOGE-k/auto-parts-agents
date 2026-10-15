/** Format system names for the product UI without changing stored or API data. */
export function displaySystemText(value: string | null | undefined): string {
  return (value ?? "")
    .replace(/erpnext_api/gi, "ERP 接口")
    .replace(/openmes_api/gi, "MES 接口")
    .replace(/erpnext/gi, "ERP 系统")
    .replace(/openmes/gi, "MES 系统");
}
