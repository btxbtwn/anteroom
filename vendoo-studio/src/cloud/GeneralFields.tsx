import { GENERAL_LISTING_FIELDS } from "../generalListingFields";
import { groupFieldsIntoSections } from "../components/listingFieldSections";

export type GeneralValues = Record<string, string | number | null>;
const sections = groupFieldsIntoSections(GENERAL_LISTING_FIELDS.filter(field => field.key !== "title"));

/** The same field order and section rules as the desktop General form. */
export function GeneralFields({ values, disabled, onChange }: {
  values: GeneralValues; disabled: boolean; onChange: (key: string, value: string | number | null) => void;
}) {
  return <>{sections.map(section => <fieldset className="general-section" key={section.id} disabled={disabled}>
    <legend>{section.title}</legend><div className="field-grid">{section.fields.map(field => <div className={field.key === "description" || field.key === "category_path" ? "full-width" : ""} key={field.key}>
      <label htmlFor={`field-${field.key}`}>{field.label.replace(/ \*$/, "")}</label>
      {field.key === "description" ? <textarea id={`field-${field.key}`} rows={6} maxLength={20000} value={values[field.key] ?? ""}
        onChange={event => onChange(field.key, event.target.value)}/> : <input id={`field-${field.key}`} type={field.type === "number" ? "number" : "text"}
          min={field.key === "quantity" ? 1 : field.type === "number" ? 0 : undefined}
          max={field.key === "quantity" ? 2147483647 : field.key === "weight_oz" ? 1000000 : field.type === "number" ? 10000000 : undefined}
          step={field.key === "quantity" ? 1 : "any"} maxLength={field.key === "category_path" ? 1000 : 200}
          value={values[field.key] ?? ""} onChange={event => onChange(field.key, field.type === "number" ? event.target.value === "" ? null : Number(event.target.value) : event.target.value)}/>}
      {field.help && <p className="field-help">{field.help}</p>}
    </div>)}</div>
  </fieldset>)}</>;
}
