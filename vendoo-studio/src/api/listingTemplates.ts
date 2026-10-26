export interface ListingTemplate { id: string; name: string; revision: number; fields: Record<string, unknown>; updated_at: string }
export interface TemplateSource { source_id: string; source_title: string; expected_basis: string; fields: Record<string, unknown> }
export interface TemplateSave { name: string; selection?: { source_id: string; expected_basis: string; fields: string[] }; expected_revision?: number }
export interface TemplateDraft { request_id: string; expected_revision: number; title: string; sku: string; quantity: number }
export interface TemplateApply { conversation_id: string; expected_template_revision: number; mode: "replace" | "fill_blank" }
export interface TemplateReview { conversation_id: string; title: string; changes: {field: string; old: unknown; new: unknown}[]; expected_revision_id: string; expected_basis: string; expected_result: string }
export type TemplateApproval = TemplateApply & Pick<TemplateReview,"expected_revision_id"|"expected_basis"|"expected_result">;
const CATEGORY_FIELDS=["marketplace_categories","marketplace_category_ids","marketplace_category_objects","direct_category_ids"];
const LABELS: Record<string,string>={ebay_specifics:"eBay form",etsy_specifics:"Etsy form",poshmark_specifics:"Poshmark form",mercari_specifics:"Mercari form",depop_specifics:"Depop form",vinted_specifics:"Vinted form",sellwild_specifics:"Sellwild form",title:"Title",description:"Description",price:"Asking price",cost:"Cost",quantity:"Quantity",brand:"Brand",condition:"Condition",primaryColor:"Primary color",secondaryColor:"Secondary color",category_path:"General category",size:"Size",sizeType:"Size type",size_us:"US size",department:"Department",tags:"Tags",labels:"Labels",weight_lb:"Package pounds",weight_oz:"Package ounces",package_dimensions_in:"Package dimensions",marketplace_categories:"Marketplace categories"};
export function templateFieldName(key: string): string { return LABELS[key] || key.replace(/_specifics$/, " form").replace(/_/g," ").replace(/^./,c=>c.toUpperCase()); }
export function templateFieldGroups(fields: Record<string,unknown>): {key:string;name:string;fields:string[];value:unknown}[] {
  const groups=Object.keys(fields).filter(key=>!CATEGORY_FIELDS.includes(key)).map(key=>({key,name:templateFieldName(key),fields:[key],value:fields[key]}));
  const categories=CATEGORY_FIELDS.filter(key=>key in fields);
  if(categories.length)groups.push({key:"marketplace_categories",name:"Marketplace categories",fields:categories,value:fields.marketplace_categories || "Saved direct marketplace selections"});
  return groups;
}
export function templateValue(value: unknown): string { return value==null ? "Not set" : typeof value==="string" ? value || "Empty" : JSON.stringify(value); }
export function templateApproval(body: TemplateApply, review: TemplateReview): TemplateApproval {
  return {...body,expected_revision_id:review.expected_revision_id,expected_basis:review.expected_basis,expected_result:review.expected_result};
}
