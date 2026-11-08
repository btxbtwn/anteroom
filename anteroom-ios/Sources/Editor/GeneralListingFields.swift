import SwiftUI

/// The General form, in the order agreed for web and iPhone: what sells the
/// item first (title, description, price), then what it is, its condition,
/// measurements, shipping, stock, and the seller's own notes last. Photos sit
/// above it. Native marketplace forms remain separate.
struct GeneralListingFields: View {
    @Binding var form: ListingEditorModel.Form
    var options = GeneralOptions()
    /// Loads the shared category tree. Without it, Category is a text field.
    var api: AnteroomAPI?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// Section titles in order, for tests and the layout review.
    static let sectionOrder = ["Listing", "Item", "Condition & details", "Measurements (in)", "Shipping",
                               "Inventory", "Notes & Labels"]

    var body: some View {
        ListingInspectorSection("Listing") {
            WorkspaceField("Title") {
                TextField("What buyers search for", text: $form.fields.title, axis: .vertical).lineLimit(1...3)
                    .accessibilityIdentifier("listing-title")
            }
            .fieldAnchor("title")
            WorkspaceField("Description") {
                TextField("Describe your item", text: $form.fields.description, axis: .vertical).lineLimit(5...16)
            }
            .fieldAnchor("description")
            pair {
                NumberField("Price", text: $form.price, prefix: "$", placeholder: "Asking price").fieldAnchor("price")
                NumberField("Cost", text: $form.cost, prefix: "$", placeholder: "What you paid").fieldAnchor("cost")
            }
        }
        ListingInspectorSection("Item") {
            GeneralCategoryField(path: $form.fields.categoryPath, api: api).fieldAnchor("category_path")
            TextRow("Brand", text: $form.fields.brand, placeholder: "e.g. Levi's").fieldAnchor("brand")
            TextRow("Department", text: $form.fields.department, placeholder: "e.g. Women, Men, Kids").fieldAnchor("department")
        }
        ListingInspectorSection("Condition & details") {
            OptionField("Condition", value: $form.fields.condition, options: options.condition).fieldAnchor("condition")
            OptionField("Size", value: $form.fields.size, options: options.size).fieldAnchor("size")
            OptionField("Primary Color", value: $form.fields.primaryColor, options: options.primaryColor).fieldAnchor("primaryColor")
            OptionField("Secondary Color", value: $form.fields.secondaryColor, options: options.secondaryColor).fieldAnchor("secondaryColor")
        }
        SellerMeasurementFields(inputs: $form.sellerInputs)
        ListingInspectorSection("Shipping") {
            // One per row: the long labels wrapped side by side and pushed the boxes out of line.
            NumberField("Package Weight (oz)", text: $form.weightOz, placeholder: "Ounces").fieldAnchor("weight_oz")
            TextRow("Package Dimensions (LxWxH in)", text: $form.fields.packageDimensionsIn, placeholder: "L × W × H")
                .fieldAnchor("package_dimensions_in")
            TextRow("Inventory location", text: $form.fields.zipCode, placeholder: "ZIP code").fieldAnchor("zipCode")
        }
        ListingInspectorSection("Inventory") {
            pair {
                WorkspaceField("Quantity") { TextField("1", text: $form.quantity).keyboardType(.numberPad) }.fieldAnchor("quantity")
                TextRow("SKU", text: $form.fields.sku, placeholder: "Your stock code").fieldAnchor("sku")
            }
        }
        SellerNoteFields(inputs: $form.sellerInputs)
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

/// Where the editor scrolls to show a field a marketplace still needs. Each
/// field is tagged with its listing path, the name the server's checks use.
enum FieldAnchor {
    /// The General form's listing paths.
    static let general: Set<String> = ["title", "description", "price", "cost", "category_path", "brand", "department",
                                       "condition", "size", "primaryColor", "secondaryColor", "weight_oz",
                                       "package_dimensions_in", "zipCode", "quantity", "sku"]

    static func id(_ path: String) -> String { "field:\(path)" }
}

extension View {
    func fieldAnchor(_ path: String) -> some View { id(FieldAnchor.id(path)) }
}
