import { describe, expect, it } from "vitest";
import { templateApproval, templateFieldGroups, templateValue, type TemplateReview } from "./listingTemplates";

describe("saved template review",()=>{
  it("groups marketplace category selections so paths and IDs stay together",()=>{
    const groups=templateFieldGroups({price:40,marketplace_categories:{ebay:"Clothing > Coats"},direct_category_ids:{ebay:"123"},marketplace_category_objects:{ebay:{id:"123"}},ebay_specifics:{Size:"M"}});
    expect(groups.map(group=>group.name)).toEqual(["Asking price","eBay form","Marketplace categories"]);
    const category=groups[2];
    expect(category.fields).toEqual(["marketplace_categories","marketplace_category_objects","direct_category_ids"]);
    expect(category.value).toEqual({ebay:"Clothing > Coats"});
  });
  it("approves only the reviewed revision, source basis and exact result",()=>{
    const review:TemplateReview={conversation_id:"coat",title:"Coat",changes:[{field:"price",old:20,new:40}],expected_revision_id:"revision",expected_basis:"a".repeat(64),expected_result:"b".repeat(64)};
    expect(templateApproval({conversation_id:"coat",expected_template_revision:3,mode:"replace"},review)).toEqual({conversation_id:"coat",expected_template_revision:3,mode:"replace",expected_revision_id:"revision",expected_basis:review.expected_basis,expected_result:review.expected_result});
  });
  it("keeps zero, blanks and unknown values distinct in the review",()=>{
    expect(templateValue(0)).toBe("0");expect(templateValue("")).toBe("Empty");expect(templateValue(null)).toBe("Not set");
  });
});
