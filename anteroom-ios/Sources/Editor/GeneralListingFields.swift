import SwiftUI

/// Mirrors the desktop General inspector's section headings and field groups
/// (listingFieldSections.ts). Native marketplace forms remain separate.
struct GeneralListingFields: View {
    @Binding var form: ListingEditorModel.Form
    var options = GeneralOptions()
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ListingInspectorSection("Listing") {
            WorkspaceField("Title") {
                TextField("Title", text: $form.fields.title, axis: .vertical).lineLimit(1...3)
            }
            WorkspaceField("Description") {
                TextField("Describe your item", text: $form.fields.description, axis: .vertical).lineLimit(5...16)
            }
        }
        ListingInspectorSection("Pricing") {
            let layout = dynamicTypeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
                : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
            layout {
                NumberField("Price", text: $form.price, prefix: "$")
                NumberField("Cost", text: $form.cost, prefix: "$")
            }
        }
        ListingInspectorSection("Condition") {
            OptionField("Condition", value: $form.fields.condition, options: options.condition)
        }
        ListingInspectorSection("Item") {
            VStack(alignment: .leading, spacing: 6) {
                TextRow("Category *", text: $form.fields.categoryPath)
                    .accessibilityHint("Required")
                Text("Applied to each marketplace form. Name the last category, such as Men > Shirts > Tees. Each marketplace form keeps its own category list.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            TextRow("Brand", text: $form.fields.brand)
            TextRow("Department", text: $form.fields.department)
        }
        ListingInspectorSection("Size & fit") {
            OptionField("Size", value: $form.fields.size, options: options.size)
        }
        ListingInspectorSection("Color & material") {
            OptionField("Primary Color", value: $form.fields.primaryColor, options: options.primaryColor)
            OptionField("Secondary Color", value: $form.fields.secondaryColor, options: options.secondaryColor)
        }
        ListingInspectorSection("Shipping") {
            NumberField("Package Weight (oz)", text: $form.weightOz)
            TextRow("Package Dimensions (LxWxH in)", text: $form.fields.packageDimensionsIn)
            TextRow("Inventory location", text: $form.fields.zipCode)
        }
        ListingInspectorSection("Inventory & identifiers") {
            WorkspaceField("Quantity") { TextField("1", text: $form.quantity).keyboardType(.numberPad) }
            TextRow("SKU", text: $form.fields.sku)
        }

    }
}

struct ListingInspectorSection<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.subheadline.weight(.semibold)).accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 16)
        .overlay(alignment: .bottom) { WorkspaceStyle.border.frame(height: 1) }
    }
}
