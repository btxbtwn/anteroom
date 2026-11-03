import SwiftUI

/// Mirrors the desktop General form's sections and order
/// (generalFormSections in listingFieldSections.ts): what the item is, how it
/// reads, its condition, then price, shipping and stock. Native marketplace
/// forms remain separate.
struct GeneralListingFields: View {
    @Binding var form: ListingEditorModel.Form
    var options = GeneralOptions()
    /// Loads the shared category tree. Without it, Category is a text field.
    var api: AnteroomAPI?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        ListingInspectorSection("Item") {
            GeneralCategoryField(path: $form.fields.categoryPath, api: api)
            TextRow("Department", text: $form.fields.department)
            TextRow("Brand", text: $form.fields.brand)
        }
        ListingInspectorSection("Listing") {
            WorkspaceField("Title") {
                TextField("Title", text: $form.fields.title, axis: .vertical).lineLimit(1...3)
            }
            WorkspaceField("Description") {
                TextField("Describe your item", text: $form.fields.description, axis: .vertical).lineLimit(5...16)
            }
        }
        ListingInspectorSection("Condition & details") {
            OptionField("Condition", value: $form.fields.condition, options: options.condition)
            OptionField("Size", value: $form.fields.size, options: options.size)
            OptionField("Primary Color", value: $form.fields.primaryColor, options: options.primaryColor)
            OptionField("Secondary Color", value: $form.fields.secondaryColor, options: options.secondaryColor)
        }
        ListingInspectorSection("Pricing") {
            pair {
                NumberField("Price", text: $form.price, prefix: "$")
                NumberField("Cost", text: $form.cost, prefix: "$")
            }
        }
        ListingInspectorSection("Shipping") {
            pair {
                NumberField("Package Weight (oz)", text: $form.weightOz)
                TextRow("Package Dimensions (LxWxH in)", text: $form.fields.packageDimensionsIn)
            }
            TextRow("Inventory location", text: $form.fields.zipCode)
        }
        ListingInspectorSection("Inventory") {
            pair {
                WorkspaceField("Quantity") { TextField("1", text: $form.quantity).keyboardType(.numberPad) }
                TextRow("SKU", text: $form.fields.sku)
            }
        }
    }

    /// Two related fields side by side; stacked at accessibility text sizes.
    @ViewBuilder private func pair<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
        layout { content() }
    }
}

/// The General category, picked from the shared category tree (the web's
/// GeneralCategorySelect). It stays a text field while the tree is unavailable.
struct GeneralCategoryField: View {
    @Binding var path: String
    let api: AnteroomAPI?
    @State private var treeReady = false
    @State private var picking = false

    private static let help = "Choose the most specific category. Each marketplace form then picks its own matching category."

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let api, treeReady {
                WorkspaceField("Category *") {
                    Button { picking = true } label: {
                        HStack {
                            Text(path.isEmpty ? "Choose a category" : CategoryBrowse.breadcrumb(path))
                                .foregroundStyle(path.isEmpty ? .secondary : .primary)
                                .multilineTextAlignment(.leading)
                            Spacer(minLength: 8)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityValue(path.isEmpty ? "Not set" : CategoryBrowse.breadcrumb(path))
                    .accessibilityIdentifier("general-category")
                }
                .sheet(isPresented: $picking) {
                    CategoryTreeSheet(api: api, marketplace: "general", title: "Category",
                                      current: CategoryBrowse.breadcrumb(path)) { leaf in
                        path = leaf.path
                    }
                }
            } else {
                TextRow("Category *", text: $path)
            }
            Text(Self.help).font(.footnote).foregroundStyle(.secondary)
        }
        .accessibilityHint("Required")
        .task {
            guard let api, !treeReady else { return }
            let tree = try? await CategoryTreeCache.shared.categories(api: api, marketplace: "general")
            treeReady = !(tree?.leaves.isEmpty ?? true)
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
