export type SchemaInputKind = "number" | "integer" | "boolean" | "enum" | "string";

export interface SchemaFormField {
  path: string;
  section: string;
  subgroup: string;
  label: string;
  inputKind: SchemaInputKind;
  isEvidence: boolean;
  evidenceState: string | null;
  value: string | boolean;
  originalValue: string | boolean;
  unit: string | null;
  enumValues: string[];
  min?: number;
  max?: number;
  exclusiveMin?: number;
  exclusiveMax?: number;
  pattern?: string;
  minLength?: number;
  required: boolean;
  description: string;
}

const SKIP_SECTIONS = new Set([
  "spatial_layers",
  "time_series",
  "schema_version",
  "scenario_id",
  "alternatives",
  "datasets",
  "networks",
  "routes",
  "policies",
  "operating_cases",
  "coupling_cases",
]);

const SKIP_FIELDS = new Set([
  "schema_version",
  "geometry",
  "parcel_geometry",
  "usable_pad_geometry",
  "study_area",
  "location",
  "elevation_profile",
  "provenance",
  "candidates",
  "candidates_list",
]);

function resolveRef(
  node: Record<string, unknown> | undefined,
  defs: Record<string, Record<string, unknown>>
): Record<string, unknown> {
  if (!node) return {};
  if (typeof node.$ref === "string") {
    const prefix = "#/$defs/";
    if (node.$ref.startsWith(prefix)) {
      const target = defs[node.$ref.slice(prefix.length)] || {};
      const resolved = resolveRef(target, defs);
      return { ...resolved, ...node, $ref: undefined };
    }
  }
  return node;
}

function isEvidenceNode(
  node: Record<string, unknown>,
  defs: Record<string, Record<string, unknown>>
): { isEvidence: boolean; innerSchema: Record<string, unknown> } {
  const title = typeof node.title === "string" ? node.title : "";
  const anyOf = Array.isArray(node.anyOf) ? (node.anyOf as Array<Record<string, unknown>>) : [];
  if (title.startsWith("Evidence[") || anyOf.length > 0) {
    for (const variant of anyOf) {
      const resolved = resolveRef(variant, defs);
      const vTitle = typeof resolved.title === "string" ? resolved.title : "";
      const props = (resolved.properties || {}) as Record<string, Record<string, unknown>>;
      if (vTitle.startsWith("Known[") || (props.state?.const === "known" && props.value)) {
        return { isEvidence: true, innerSchema: resolveRef(props.value, defs) };
      }
    }
  }
  const props = (node.properties || {}) as Record<string, Record<string, unknown>>;
  if (title.startsWith("Known[") && props.value) {
    return { isEvidence: true, innerSchema: resolveRef(props.value, defs) };
  }
  return { isEvidence: false, innerSchema: node };
}

function unwrapNullable(
  node: Record<string, unknown>,
  defs: Record<string, Record<string, unknown>>
): { schema: Record<string, unknown>; nullable: boolean } {
  const anyOf = Array.isArray(node.anyOf) ? (node.anyOf as Array<Record<string, unknown>>) : null;
  if (!anyOf) return { schema: resolveRef(node, defs), nullable: false };
  const nonNull = anyOf
    .map((item) => resolveRef(item, defs))
    .filter((item) => item.type !== "null");
  if (nonNull.length === 1) {
    return {
      schema: { ...nonNull[0], description: node.description ?? nonNull[0].description, unit: node.unit ?? nonNull[0].unit },
      nullable: anyOf.some((item) => resolveRef(item, defs).type === "null"),
    };
  }
  return { schema: node, nullable: false };
}

function identityOfItem(item: Record<string, unknown>): string | null {
  for (const [k, v] of Object.entries(item)) {
    if (k.endsWith("_id") && typeof v === "string" && v.length > 0) {
      return v;
    }
  }
  return null;
}

function humanizeField(name: string): string {
  return name.replace(/_/g, " ");
}

function unitFromNameOrSchema(fieldName: string, schema: Record<string, unknown>): string | null {
  if (typeof schema.unit === "string" && schema.unit) return schema.unit;
  if (fieldName.endsWith("_k")) return "K";
  if (fieldName.endsWith("_delta_k")) return "delta_K";
  if (fieldName.endsWith("_pa")) return "Pa";
  if (fieldName.endsWith("_w")) return "W";
  if (fieldName.endsWith("_kg_s")) return "kg/s";
  if (fieldName.endsWith("_m2")) return "m2";
  if (fieldName.endsWith("_m3")) return "m3";
  if (fieldName.endsWith("_m")) return "m";
  if (fieldName.endsWith("_s")) return "s";
  if (fieldName.endsWith("_years")) return "years";
  if (fieldName.endsWith("_per_year")) return "1/year";
  if (fieldName.endsWith("_fraction")) return "1";
  if (fieldName.endsWith("_currency")) return "EUR";
  return null;
}

export function extractSchemaFormFields(
  rootSchema: Record<string, unknown>,
  scenarioData: Record<string, unknown>
): SchemaFormField[] {
  const defs = ((rootSchema.$defs || {}) as Record<string, Record<string, unknown>>) || {};
  const rootProps = ((rootSchema.properties || {}) as Record<string, Record<string, unknown>>) || {};
  const fields: SchemaFormField[] = [];

  const visitNode = (
    rawNode: Record<string, unknown>,
    data: unknown,
    path: string,
    section: string,
    subgroup: string,
    fieldName: string,
    required: boolean,
    depth: number
  ): void => {
    if (depth > 5 || SKIP_FIELDS.has(fieldName)) return;
    if (fieldName.endsWith("_id") && path.includes("[")) return;

    const resolvedTop = resolveRef(rawNode, defs);
    const { isEvidence, innerSchema } = isEvidenceNode(resolvedTop, defs);
    let evidenceState: string | null = null;
    let leafData: unknown = data;

    if (isEvidence && data && typeof data === "object" && !Array.isArray(data)) {
      const evObj = data as Record<string, unknown>;
      evidenceState = typeof evObj.state === "string" ? evObj.state : null;
      leafData = evidenceState === "known" ? evObj.value : "";
    }

    const { schema, nullable } = unwrapNullable(
      isEvidence ? innerSchema : resolvedTop,
      defs
    );
    const effectiveUnit = unitFromNameOrSchema(fieldName, {
      ...resolvedTop,
      ...schema,
    });

    if (Array.isArray(schema.enum)) {
      const val = leafData === undefined || leafData === null ? "" : String(leafData);
      fields.push({
        path,
        section,
        subgroup,
        label: humanizeField(fieldName),
        inputKind: "enum",
        isEvidence,
        evidenceState,
        value: val,
        originalValue: val,
        unit: effectiveUnit,
        enumValues: schema.enum.map(String),
        required: required && !nullable,
        description: String(resolvedTop.description || schema.description || ""),
      });
      return;
    }

    if (schema.type === "boolean") {
      const boolVal = Boolean(leafData);
      fields.push({
        path,
        section,
        subgroup,
        label: humanizeField(fieldName),
        inputKind: "boolean",
        isEvidence,
        evidenceState,
        value: boolVal,
        originalValue: boolVal,
        unit: effectiveUnit,
        enumValues: [],
        required: false,
        description: String(resolvedTop.description || schema.description || ""),
      });
      return;
    }

    if (schema.type === "number" || schema.type === "integer") {
      const numStr =
        typeof leafData === "number" && Number.isFinite(leafData) ? String(leafData) : "";
      fields.push({
        path,
        section,
        subgroup,
        label: humanizeField(fieldName),
        inputKind: schema.type === "integer" ? "integer" : "number",
        isEvidence,
        evidenceState,
        value: numStr,
        originalValue: numStr,
        unit: effectiveUnit,
        enumValues: [],
        min: typeof schema.minimum === "number" ? schema.minimum : undefined,
        max: typeof schema.maximum === "number" ? schema.maximum : undefined,
        exclusiveMin:
          typeof schema.exclusiveMinimum === "number" ? schema.exclusiveMinimum : undefined,
        exclusiveMax:
          typeof schema.exclusiveMaximum === "number" ? schema.exclusiveMaximum : undefined,
        required: required && !nullable && (!isEvidence || evidenceState === "known"),
        description: String(resolvedTop.description || schema.description || ""),
      });
      return;
    }

    if (schema.type === "string") {
      const textVal = leafData === undefined || leafData === null ? "" : String(leafData);
      fields.push({
        path,
        section,
        subgroup,
        label: humanizeField(fieldName),
        inputKind: "string",
        isEvidence,
        evidenceState,
        value: textVal,
        originalValue: textVal,
        unit: effectiveUnit,
        enumValues: [],
        pattern: typeof schema.pattern === "string" ? schema.pattern : undefined,
        minLength: typeof schema.minLength === "number" ? schema.minLength : undefined,
        required: required && !nullable && (!isEvidence || evidenceState === "known"),
        description: String(resolvedTop.description || schema.description || ""),
      });
      return;
    }

    // Object with declared properties or additionalProperties dict (e.g. reservoir_parameters)
    if (schema.type === "object" && data && typeof data === "object" && !Array.isArray(data)) {
      const dataObj = data as Record<string, unknown>;
      const props = (schema.properties || {}) as Record<string, Record<string, unknown>>;
      const reqList = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
      if (Object.keys(props).length > 0) {
        for (const [childKey, childSchema] of Object.entries(props)) {
          if (childKey in dataObj) {
            visitNode(
              childSchema,
              dataObj[childKey],
              `${path}.${childKey}`,
              section,
              subgroup,
              childKey,
              reqList.has(childKey),
              depth + 1
            );
          }
        }
      } else if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
        const valSchema = schema.additionalProperties as Record<string, unknown>;
        for (const [dictKey, dictVal] of Object.entries(dataObj)) {
          visitNode(
            valSchema,
            dictVal,
            `${path}[${dictKey}]`,
            section,
            subgroup,
            dictKey,
            false,
            depth + 1
          );
        }
      }
      return;
    }

    // Array of keyed records
    if (schema.type === "array" && Array.isArray(data) && schema.items && typeof schema.items === "object") {
      const itemSchema = schema.items as Record<string, unknown>;
      for (let i = 0; i < data.length; i += 1) {
        const entry = data[i];
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
        const entryObj = entry as Record<string, unknown>;
        const key = identityOfItem(entryObj);
        if (!key) continue;
        visitNode(
          itemSchema,
          entryObj,
          `${path}[${key}]`,
          section,
          key,
          key,
          false,
          depth + 1
        );
      }
    }
  };

  const rootReq = new Set(Array.isArray(rootSchema.required) ? rootSchema.required.map(String) : []);
  for (const [sectionName, propSchema] of Object.entries(rootProps)) {
    if (SKIP_SECTIONS.has(sectionName)) continue;
    if (!(sectionName in scenarioData)) continue;
    visitNode(
      propSchema,
      scenarioData[sectionName],
      sectionName,
      sectionName,
      sectionName,
      sectionName,
      rootReq.has(sectionName),
      0
    );
  }

  return fields;
}

export function buildPatchFromFieldEdit(
  field: SchemaFormField,
  newValue: string | boolean
): { op: "set" | "set_known"; path: string; value: unknown } {
  let typed: unknown = newValue;
  if (field.inputKind === "boolean") {
    typed = Boolean(newValue);
  } else if (field.inputKind === "integer") {
    typed = Number.parseInt(String(newValue), 10);
  } else if (field.inputKind === "number") {
    typed = Number.parseFloat(String(newValue));
  } else {
    typed = String(newValue);
  }

  return {
    op: field.isEvidence ? "set_known" : "set",
    path: field.path,
    value: typed,
  };
}
