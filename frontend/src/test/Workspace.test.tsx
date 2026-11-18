import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import App from "../App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeFixtures() {
  const baseRev = "rev-base-1111";
  const varRev = "rev-var-2222";
  const run1 = "run-0001";

  const assumptions = [
    {
      field_path: "economic_scenarios[syn-econ].discount_rate_per_year",
      status: "active",
      value: 0.04,
      unit: "1/year",
      provenance_type: "synthetic",
      source_id: "syn-definitions",
      locator: "section 5",
      note: "Real discount rate",
    },
    {
      field_path: "sites[delta].usable_pad_area_m2",
      status: "unknown",
      value: null,
      unit: "m2",
      provenance_type: null,
      source_id: null,
      locator: null,
      note: "Survey pending",
      owner: "Site specialist",
    },
  ];

  const schema = {
    $defs: {
      EvidenceFloat: {
        title: "Evidence[float]",
        anyOf: [
          {
            title: "Known[float]",
            type: "object",
            properties: {
              state: { const: "known", type: "string" },
              value: { type: "number", minimum: 0 },
            },
          },
          { type: "object", properties: { state: { const: "unknown" } } },
        ],
      },
      EconomicScenario: {
        type: "object",
        required: ["economic_scenario_id", "discount_rate_per_year", "horizon_years"],
        properties: {
          economic_scenario_id: { type: "string" },
          discount_rate_per_year: { $ref: "#/$defs/EvidenceFloat" },
          horizon_years: { $ref: "#/$defs/EvidenceFloat", minimum: 1, maximum: 60 },
        },
      },
    },
    properties: {
      economic_scenarios: {
        type: "array",
        items: { $ref: "#/$defs/EconomicScenario" },
      },
    },
    required: ["economic_scenarios"],
  };

  const baseScenario = {
    economic_scenarios: [
      {
        economic_scenario_id: "syn-econ",
        discount_rate_per_year: { state: "known", value: 0.04 },
        horizon_years: { state: "known", value: 20 },
      },
    ],
  };

  const runEvidencePayload = {
    scenario_revision: baseRev,
    synthetic: true,
    explanation: {
      records: [
        {
          explanation_id: "exp-cand-alpha",
          kind: "result",
          subject: "cand-alpha",
          study_id: "synthetic-study",
          scenario_revision: baseRev,
          run_id: run1,
          synthetic: true,
          all_claims_accepted: true,
          claims: [
            {
              claim_id: "c1",
              claim_type: "value",
              subject: "cand-alpha",
              result_id: "res-cand-alpha",
              metric_id: "lcoh_geothermal_delivered_currency_per_j",
              value: 0.0000000145,
              unit: "EUR/J",
              source_ref: "run-0001",
            },
          ],
          check_outcomes: [{ claim_id: "c1", accepted: true, reasons: [] }],
        },
      ],
      rendered: {
        "exp-cand-alpha": "[SYNTHETIC] cand-alpha passes with LCOH 52.20 EUR/MWh.",
      },
    },
    data: {
      results: [
        {
          result_id: "res-study",
          subject: "study",
          stage_outputs: [
            {
              stage: "assess",
              payload: {
                assessments: [
                  {
                    candidate_id: "cand-beta",
                    label: "Beta direct heat",
                    technical_status: "fails_thermal_limit",
                    evaluation_status: "valid",
                    ranking_eligibility: {
                      eligible: false,
                      conditional: false,
                      reasons: ["exchanger approach temperature failed"],
                    },
                    explanation: ["Terminal approach failed"],
                  },
                  {
                    candidate_id: "cand-alpha",
                    label: "Alpha direct heat",
                    technical_status: "passes_evaluated_cases",
                    evaluation_status: "valid",
                    ranking_eligibility: {
                      eligible: true,
                      conditional: false,
                      reasons: [],
                    },
                    explanation: ["All cases passed"],
                  },
                ],
                rejected: [],
              },
            },
            {
              stage: "operate",
              payload: {
                candidates: {
                  "cand-alpha": {
                    totals: {
                      geothermal_delivered_heat_j: 3.6e13,
                      backup_delivered_heat_j: 1.8e12,
                    },
                  },
                },
              },
            },
            {
              stage: "cost",
              payload: {
                candidates: {
                  "cand-alpha": {
                    boundaries: {
                      geothermal_delivered: {
                        lcoh_currency_per_j: 52.2 / 3.6e9,
                        capex_currency: 12000000,
                        currency: "EUR",
                      },
                      system_delivered: {
                        lcoh_currency_per_j: 58.1 / 3.6e9,
                        currency: "EUR",
                      },
                    },
                    baseline_comparison: {
                      incremental_discounted_cost_currency: -1500000,
                    },
                  },
                },
              },
            },
            {
              stage: "compare",
              payload: {
                comparison: {
                  preference_outcome: "preferred_candidate",
                  preference_reason: "cand-alpha leads within tolerance",
                  ranking_stability: [
                    { candidate_id: "cand-alpha", first_rank_count: 10, denominator: 10 },
                  ],
                  decision_drivers: [
                    {
                      variable_id: "reservoir_temperature_k",
                      spearman_rank_correlation: -0.82,
                      switches_winner: true,
                    },
                  ],
                  candidates: {},
                },
                resilience: {
                  "cand-alpha": [
                    {
                      case_id: "source-outage",
                      unserved_heat_j: 0,
                      unserved_hours: 0,
                      largest_deficit_w: 0,
                    },
                  ],
                },
              },
            },
          ],
        },
        { result_id: "res-cand-alpha", subject: "cand-alpha" },
      ],
    },
  };

  return { baseRev, varRev, run1, assumptions, schema, baseScenario, runEvidencePayload };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function installFetchMock() {
  const f = makeFixtures();
  let currentLatest = f.baseRev;

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const headers = (init?.headers || {}) as Record<string, string>;
      if (headers["X-R3CHAIN-Principal"] === "outsider" && !url.endsWith("/studies")) {
        return jsonResponse(
          { status: "denied_access", failure_class: "denied_access", message: "forbidden" },
          403
        );
      }

      if (url.endsWith("/studies") && (!init?.method || init.method === "GET")) {
        if (headers["X-R3CHAIN-Principal"] === "outsider") {
          return jsonResponse({ data: { studies: [] } });
        }
        return jsonResponse({
          data: {
            studies: [
              {
                study_id: "synthetic-study",
                project_id: "r3chain",
                title: "Synthetic four consumer study",
                objective: "Test study",
                baseline_id: "baseline-existing",
                planning_date: "2026-11-15",
                crs: "EPSG:25832",
                synthetic: true,
                latest_revision: currentLatest,
                runs_count: 1,
              },
            ],
          },
        });
      }

      if (url.includes("/revisions") && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          data: {
            latest_revision: currentLatest,
            revisions: [
              {
                revision_id: f.baseRev,
                scenario_id: "synthetic-study",
                scenario_hash: "sha256:1111",
                parent_revision_id: null,
                created_at: "2026-11-17T10:00:00Z",
                author: "seed",
                rationale: "baseline",
                changes: [],
                is_latest: currentLatest === f.baseRev,
                runs: [
                  {
                    run_id: f.run1,
                    status: "completed",
                    created_at: "2026-11-17T10:05:00Z",
                    finished_at: "2026-11-17T10:05:10Z",
                    result_hash: "sha256:aaaa",
                    is_stale: currentLatest !== f.baseRev,
                    seed: 0,
                  },
                ],
              },
              ...(currentLatest === f.varRev
                ? [
                    {
                      revision_id: f.varRev,
                      scenario_id: "synthetic-study",
                      scenario_hash: "sha256:2222",
                      parent_revision_id: f.baseRev,
                      created_at: "2026-11-17T11:00:00Z",
                      author: "planner",
                      rationale: "longer horizon",
                      changes: [
                        {
                          field_path: "economic_scenarios[syn-econ].horizon_years",
                          kind: "changed",
                          before: 20,
                          after: 30,
                        },
                      ],
                      is_latest: true,
                      runs: [],
                    },
                  ]
                : []),
            ],
          },
        });
      }

      if (url.endsWith("/revisions") && init?.method === "POST") {
        currentLatest = f.varRev;
        return jsonResponse(
          {
            status: "completed",
            scenario_revision: f.varRev,
            data: { revision: { revision_id: f.varRev } },
          },
          201
        );
      }

      if (url.includes("/editor")) {
        return jsonResponse({
          status: "completed",
          scenario_revision: currentLatest,
          data: {
            revision: {
              revision_id: currentLatest,
              scenario_id: "synthetic-study",
              scenario_hash: "sha256:1111",
              parent_revision_id: null,
              created_at: "2026-11-17T10:00:00Z",
              author: "seed",
              rationale: "baseline",
              changes: [],
              is_latest: true,
              runs: [],
            },
            scenario:
              currentLatest === f.varRev
                ? {
                    economic_scenarios: [
                      {
                        economic_scenario_id: "syn-econ",
                        discount_rate_per_year: { state: "known", value: 0.04 },
                        horizon_years: { state: "known", value: 30 },
                      },
                    ],
                  }
                : f.baseScenario,
            validation: {
              executable: true,
              contains_synthetic_values: true,
              issues: [],
              missing_inputs: [
                {
                  field_path: "sites[delta].usable_pad_area_m2",
                  status: "unknown",
                  reason: "Survey pending",
                  owner: "Site specialist",
                },
              ],
            },
            schema: f.schema,
            available_policies: [],
          },
        });
      }

      if (url.includes("/map")) {
        return jsonResponse({
          status: "completed",
          data: {
            bbox: [9.0, 51.45, 9.07, 51.49],
            hidden_spatial_layers_count: 0,
            layers: {
              study_area: { type: "FeatureCollection", name: "study_area", features: [] },
              sites: {
                type: "FeatureCollection",
                name: "sites",
                features: [
                  {
                    type: "Feature",
                    properties: { site_id: "alpha", screening_status: "pass" },
                    geometry: {
                      type: "Polygon",
                      coordinates: [
                        [
                          [9.01, 51.46],
                          [9.02, 51.46],
                          [9.02, 51.47],
                          [9.01, 51.47],
                          [9.01, 51.46],
                        ],
                      ],
                    },
                  },
                ],
              },
              targets: { type: "FeatureCollection", name: "targets", features: [] },
              restrictions: { type: "FeatureCollection", name: "restrictions", features: [] },
              corridors: { type: "FeatureCollection", name: "corridors", features: [] },
              barriers: { type: "FeatureCollection", name: "barriers", features: [] },
              demand: { type: "FeatureCollection", name: "demand", features: [] },
              connections: { type: "FeatureCollection", name: "connections", features: [] },
              routes: { type: "FeatureCollection", name: "routes", features: [] },
              candidates: { type: "FeatureCollection", name: "candidates", features: [] },
            },
            text_summary: [
              {
                kind: "site",
                id: "alpha",
                label: "Synthetic site Alpha",
                status: "pass",
                details: "land=available",
              },
            ],
          },
        });
      }

      if (url.includes(`/runs/${f.run1}/evidence`)) {
        const isDe = url.includes("lang=de");
        return jsonResponse({
          ...f.runEvidencePayload,
          explanation: {
            ...f.runEvidencePayload.explanation,
            rendered: {
              "exp-cand-alpha": isDe
                ? "[SYNTHETISCH] cand-alpha besteht mit LCOH 52.20 EUR/MWh."
                : "[SYNTHETIC] cand-alpha passes with LCOH 52.20 EUR/MWh.",
            },
          },
        });
      }

      if (url.includes(`/runs/${f.run1}/replay`)) {
        return jsonResponse({
          status: "completed",
          data: { replay: { replay_run_id: "run-replay-1", identical: true } },
        });
      }

      if (url.endsWith("/exports")) {
        return jsonResponse({
          status: "completed",
          data: { content: "# Report\ncand-alpha 52.20 EUR/MWh", format: "markdown", lang: "en" },
        });
      }

      if (
        url.includes(`/studies/synthetic-study`) &&
        !url.includes("/revisions") &&
        !url.includes("/editor") &&
        !url.includes("/map") &&
        !url.includes("/evidence")
      ) {
        return jsonResponse({
          status: "completed",
          scenario_revision: currentLatest,
          synthetic: true,
          assumptions_used: f.assumptions,
          missing_inputs: [
            {
              field_path: "sites[delta].usable_pad_area_m2",
              status: "unknown",
              reason: "Survey pending",
              owner: "Site specialist",
            },
          ],
          data: {
            study: {
              study_id: "synthetic-study",
              title: "Synthetic four consumer study",
              baseline_id: "baseline-existing",
              study_area: { crs: "EPSG:25832" },
            },
            well_scenarios: ["source-alpha"],
          },
        });
      }

      return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return { f };
}

describe("R3CHAIN Planner Workspace (Step 6.2)", () => {
  it("renders map, mandatory assumptions across modes (W24-W26), eligibility before cost, and creates revision marking runs stale (W22, A15)", async () => {
    installFetchMock();
    const user = userEvent.setup({ delay: null });
    render(<App />);

    await waitFor(() =>
      expect(screen.getByText(/SYNTHETIC TEST DATA/i)).toBeInTheDocument()
    );
    const mapPanel = screen.getByRole("tabpanel", { name: /Map & Sites/i });
    expect(within(mapPanel).getByText(/Synthetic site Alpha/)).toBeInTheDocument();
    expect(within(mapPanel).getByText("sites[delta].usable_pad_area_m2")).toBeInTheDocument();

    // Presentation preference: Summary vs Technical retains mandatory gap warnings (W26)
    await user.selectOptions(screen.getByLabelText(/Presentation detail/i), "technical");
    expect(within(mapPanel).getByText("sites[delta].usable_pad_area_m2")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Presentation detail/i), "summary");
    expect(within(mapPanel).getByText("sites[delta].usable_pad_area_m2")).toBeInTheDocument();

    // Comparison view: eligibility before cost ranking (§18.1)
    await user.click(screen.getByRole("tab", { name: /Eligibility & Comparison/i }));
    const comparePanel = screen.getByRole("tabpanel", { name: /Eligibility & Comparison/i });
    const headings = within(comparePanel)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(headings[0]).toMatch(/1\. Eligibility/);
    expect(headings[1]).toMatch(/2\. Comparable Cost/);
    expect(within(comparePanel).getByText(/exchanger approach temperature failed/)).toBeInTheDocument();
    expect(within(comparePanel).getByText("52.20 EUR/MWh")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/Heat cost display/i), "GJ");
    expect(within(comparePanel).getByText("14.50 EUR/GJ")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Heat cost display/i), "MWh");

    // Schema-generated editor -> create new immutable revision -> stale warning appears (W22, A15)
    await user.click(screen.getByRole("tab", { name: /Assumptions & Editor/i }));
    const editorPanel = screen.getByRole("tabpanel", { name: /Assumptions & Editor/i });
    const horizonInput = await within(editorPanel).findByLabelText(/horizon years/i);
    await user.clear(horizonInput);
    await user.type(horizonInput, "30");
    await user.type(screen.getByLabelText(/Reason for revision change/i), "Extend plant life");
    await user.click(screen.getByRole("button", { name: /Create new immutable revision/i }));

    await waitFor(() =>
      expect(screen.getByText(/Stale result warning/i)).toBeInTheDocument()
    );
  }, 15000);

  it("supports keyboard tabs, DE/EN fixed explanations, independent replay (A17), access scoping (A16), and passes axe (§18.4)", async () => {
    installFetchMock();
    const user = userEvent.setup({ delay: null });
    const { container } = render(<App />);

    await waitFor(() =>
      expect(screen.getByText(/SYNTHETIC TEST DATA/i)).toBeInTheDocument()
    );

    // Keyboard tab navigation (ArrowRight moves from Map to Editor tab) (§18.4)
    const mapTab = screen.getByRole("tab", { name: /Map & Sites/i });
    mapTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Assumptions & Editor/i })).toHaveAttribute(
      "aria-selected",
      "true"
    );

    // Explanations & Replay (A17) + German language toggle (§18.4)
    await user.click(screen.getByRole("tab", { name: /Explanations & Export/i }));
    expect(screen.getByText(/\[SYNTHETIC\] cand-alpha passes/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Language/i), "de");
    await waitFor(() =>
      expect(screen.getByText(/\[SYNTHETISCH\] cand-alpha besteht/)).toBeInTheDocument()
    );
    expect(document.documentElement.lang).toBe("de");
    await user.selectOptions(screen.getByLabelText(/Sprache/i), "en");

    await user.click(screen.getByRole("button", { name: /Verify deterministic replay/i }));
    await waitFor(() =>
      expect(screen.getByText(/Replay verified identically/i)).toBeInTheDocument()
    );

    const axeResults = await axe.run(container);
    const serious = axeResults.violations.filter(
      (v) => v.impact === "critical" || v.impact === "serious"
    );
    expect(serious).toEqual([]);

    // Access scope switch to External Outsider hides protected/synthetic study list (A16)
    await user.selectOptions(screen.getByLabelText(/Access scope/i), "outsider");
    await waitFor(() =>
      expect(within(screen.getByLabelText(/Active study/i)).getByText("None")).toBeInTheDocument()
    );
  });
});
