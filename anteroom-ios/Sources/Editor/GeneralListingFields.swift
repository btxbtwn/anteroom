import SwiftUI

/// Mirrors the desktop General form's sections and order
/// (generalFormSections in listingFieldSections.ts): the order a seller fills
/// it in by hand. Category first, then title, condition, size and brand, the
/// price, the description, the remaining item details and shipping. Cost, SKU
/// and quantity wait under a folded More details. Native marketplace forms
/// remain separate.
struct GeneralListingFields: View {
    @Binding var form: ListingEditorModel.Form
    var options = GeneralOptions()
    /// Loads the shared category tree. Without it, Category is a text field.
    var api: AnteroomAPI?
    var focusPath: String? = nil
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var showsMoreDetails = false

    var body: some View {
        ListingInspectorSection("Item") {
            GeneralCategoryField(path: $form.fields.categoryPath, api: api).fieldAnchor("category_path")
            WorkspaceField("Title") {
                TextField("What buyers search for", text: $form.fields.title, axis: .vertical).lineLimit(1...3)
                    .accessibilityIdentifier("listing-title").fieldAnchor("title")
            }
        }
        ListingInspectorSection("Condition & details") {
            OptionField("Condition", value: $form.fields.condition, options: options.condition).fieldAnchor("condition")
            OptionField("Size", value: $form.fields.size, options: options.size).fieldAnchor("size")
            TextRow("Brand", text: $form.fields.brand, placeholder: "e.g. Levi's").fieldAnchor("brand")
        }
        ListingInspectorSection("Pricing") {
            NumberField("Price", text: $form.price, prefix: "$", placeholder: "Asking price").fieldAnchor("price")
        }
        ListingInspectorSection("Description") {
            WorkspaceField("Description") {
                TextField("Describe your item", text: $form.fields.description, axis: .vertical).lineLimit(5...16).fieldAnchor("description")
            }
        }
        ListingInspectorSection("Color & department") {
            OptionField("Primary Color", value: $form.fields.primaryColor, options: options.primaryColor).fieldAnchor("primaryColor")
            OptionField("Secondary Color", value: $form.fields.secondaryColor, options: options.secondaryColor).fieldAnchor("secondaryColor")
            TextRow("Department", text: $form.fields.department, placeholder: "e.g. Women, Men, Kids").fieldAnchor("department")
        }
        SellerMeasurementFields(inputs: $form.sellerInputs)
        ListingInspectorSection("Shipping") {
            NumberField("Package Weight (oz)", text: $form.weightOz, placeholder: "Ounces").fieldAnchor("weight_oz")
            TextRow("Package Dimensions (LxWxH in)", text: $form.fields.packageDimensionsIn, placeholder: "L × W × H").fieldAnchor("package_dimensions_in")
            TextRow("Inventory location", text: $form.fields.zipCode, placeholder: "ZIP code").fieldAnchor("zipCode")
        }
        ListingInspectorSection("More details", isExpanded: $showsMoreDetails) {
            pair {
                NumberField("Cost", text: $form.cost, prefix: "$", placeholder: "What you paid").fieldAnchor("cost")
                TextRow("SKU", text: $form.fields.sku, placeholder: "Your stock code").fieldAnchor("sku")
            }
            WorkspaceField("Quantity") { TextField("1", text: $form.quantity).keyboardType(.numberPad) }.fieldAnchor("quantity")
        }
        SellerNoteFields(inputs: $form.sellerInputs)
            .onChange(of: focusPath, initial: true) { _, path in
                if ["cost", "sku", "quantity"].contains(path ?? "") { showsMoreDetails = true }
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
    /// When set, the title is a disclosure button and the content folds away.
    let isExpanded: Binding<Bool>?
    let content: Content

    init(_ title: String, isExpanded: Binding<Bool>? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.isExpanded = isExpanded
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let isExpanded {
                Button {
                    withAnimation(.snappy) { isExpanded.wrappedValue.toggle() }
                } label: {
                    HStack(spacing: 4) {
                        Text(title).font(.subheadline.weight(.semibold))
                        Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(isExpanded.wrappedValue ? 90 : 0))
                    }
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(.isHeader)
                .accessibilityValue(isExpanded.wrappedValue ? "Expanded" : "Collapsed")
                if isExpanded.wrappedValue { content }
            } else {
                Text(title).font(.subheadline.weight(.semibold)).accessibilityAddTraits(.isHeader)
                content
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 16)
        .overlay(alignment: .bottom) { WorkspaceStyle.border.frame(height: 1) }
    }
}

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
