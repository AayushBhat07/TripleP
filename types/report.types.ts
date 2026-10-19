/**
 * The analysis report as the Python backend sends it (backend/proposalpanda/engine): the tender summary,
 * the compliance score, and the two combined. Types only; the screens read these, the backend writes them.
 */

import type { BoqPart } from '@/lib/tenders/boq';
import type { KeyDate } from '@/lib/tenders/dates';

/**
 * Structured summarization output
 * All sections are factual compressions, no judgment or scoring
 */
export interface TenderSummary {
  /**
   * High-level project overview
   * - Scope of work
   * - Authority & location
   * - Contract value & duration
   */
  executiveSummary: string;

  /**
   * Key commercial terms
   * - EMD amount
   * - Performance security
   * - Completion period
   * - Defect liability period
   * - Tender type
   */
  commercialTerms: string;

  /**
   * Important dates and obligations
   * - Submission requirements
   * - Validity requirements
   * - Extension obligations (if mentioned)
   */
  datesAndObligations: string;

  /**
   * Technical scope overview
   * - Nature of works
   * - Major work categories (earthwork, concrete, drainage, etc.)
   * - Execution complexity (descriptive, not scored)
   */
  technicalScope: string;

  /**
   * Legal and contractual highlights
   * - Bond requirements
   * - Guarantee obligations
   * - Authority hierarchy
   * - Jurisdiction references
   */
  legalHighlights: string;

  /**
   * Risks and attention points (FACTUAL ONLY)
   * - Long execution period
   * - High security requirements
   * - Extensive technical scope
   * - Any repeated obligations
   * NOTE: This is NOT a risk assessment, only factual summary
   */
  attentionPoints: string;

  /**
   * Eligibility criteria and key contract clauses
   * - Similar works, turnover, solvency, bid capacity
   * - Delay compensation and cap, price variation, advances
   * - Dispute resolution
   */
  eligibilityAndClauses?: string;

  /** Start of the extracted tender text, so bid drafting can cite exact terms. */
  sourceText?: string;

  /**
   * Metadata
   */
  metadata: {
    tenderId: string;
    tenderTitle: string;
    generatedAt: Date;
    /** NIT / tender reference number found in the document, if any */
    nitReference?: string;
    /** Completion period in months found in the document, if any */
    completionMonths?: number;
    /** EMD as printed, e.g. "Rs. 27,24,900", if found */
    emdAmount?: string;
    /** Estimated cost put to tender as printed, if found */
    estimatedCost?: string;
    /** Compensation for delay, e.g. "1.5% per month, maximum 10%", if found */
    delayCompensation?: string;
    /** Tender inviting office, e.g. "Executive Engineer, Pune Central Division-II, ...", if found */
    invitingOffice?: string;
    /** Delay, price variation, advance, security deposit and guarantee terms quoted from the tender text */
    keyTerms?: Array<{ label: string; text: string }>;
    /** Two quotes that set the performance guarantee differently */
    guaranteeConflict?: [string, string];
    /** Eligibility rules quoted from the tender: enlistment, similar works, turnover, solvency */
    eligibility?: Array<{ label: string; text: string }>;
    /** Risk-bearing clauses (guarantees, forfeiture, finality, disputes, extensions...) quoted from the tender text */
    riskClauses?: Array<{ label: string; text: string }>;
    /** Works (build/repair), supply (goods, GeM) or services (manpower, security, consultancy, O&M) */
    tenderKind?: 'works' | 'supply' | 'services';
    /** IS codes and concrete / steel grades mentioned anywhere in the tender, to check drafts against */
    standards?: { isCodes: string[]; grades: string[] };
    /** Bid due, opening, pre-bid and query dates read from the NIT (or corrected by hand on the analysis page) */
    keyDates?: KeyDate[];
    /** The NIT prints "As per CPP portal" in place of its dates */
    datesOnPortal?: boolean;
    /** Schedules of quantities printed in the tender, read row by row; empty when the BOQ is a separate file */
    boq?: BoqPart[];
    /** Name of work / subject as printed in the NIT, if found */
    nameOfWork?: string;
    /** For works tenders: a building, infrastructure (road, bridge, drain...) or maintenance / repair contract */
    worksType?: 'building' | 'infrastructure' | 'maintenance';
    /**
     * Ollama model tag; "<model> (+N extractive)" when N sections fell back;
     * 'extractive-fallback' when no local model was reachable
     */
    modelUsed: string;
    totalChunks: number;
    processingTimeMs: number;
  };
}

/**
 * Risk level classification
 */
export type RiskLevel = 'Low' | 'Medium' | 'High';

/**
 * Risk categories for classification
 */
export type RiskCategory = 'Financial' | 'Technical' | 'Legal' | 'Submission';

/**
 * Identified risk with source traceability
 */
export interface IdentifiedRisk {
  /**
   * Risk category
   */
  category: RiskCategory;

  /**
   * Factual description of the risk
   * ALLOWED: "The tender specifies...", "The document states..."
   * FORBIDDEN: "should", "recommended", "unfair", "bad tender"
   */
  description: string;

  /**
   * Source section from Phase 4A summary
   * e.g., "executiveSummary", "legalHighlights", "commercialTerms"
   */
  sourceSection: string;
}

/**
 * Missing or weak clause identification
 */
export interface MissingClause {
  /**
   * Name or description of the clause
   */
  clause: string;

  /**
   * Factual reason for concern (no opinion)
   */
  reason: string;
}

/**
 * Structured compliance scoring output
 * EXACT structure required - do not modify
 */
export interface ComplianceScore {
  /**
   * Overall compliance score (0-100)
   * Higher = more compliant/less risky
   * Same input → same output (deterministic)
   */
  complianceScore: number;

  /**
   * Overall risk level based on aggregate analysis
   */
  riskLevel: RiskLevel;

  /**
   * Risk breakdown by category
   */
  riskCategories: {
    financial: RiskLevel;
    technical: RiskLevel;
    legal: RiskLevel;
    submission: RiskLevel;
  };

  /**
   * Identified risks with factual descriptions
   */
  identifiedRisks: IdentifiedRisk[];

  /**
   * Missing or weak clauses that increase risk
   */
  missingOrWeakClauses: MissingClause[];

  /**
   * Submission traps (factual observations)
   * e.g., "Requires unconditional bank guarantee within 3 days"
   */
  submissionTraps: string[];

  /**
   * Model confidence and analysis notes
   * Explains scoring rationale
   */
  confidenceNotes: string;
}

/**
 * Unified intelligence output
 * Combines Phase 4A and Phase 4B results with execution metadata
 */
export interface IntelligenceReport {
  /**
   * Phase 4A: Structured tender summary
   */
  summary: TenderSummary;

  /**
   * Phase 4B: Compliance and risk scoring
   */
  compliance: ComplianceScore;

  /**
   * Pipeline execution metadata
   */
  metadata: {
    /**
     * ISO timestamp when report was generated
     */
    generatedAt: string;

    /**
     * Pipeline version identifier
     */
    pipelineVersion: '4A+4B';

    /**
     * Total execution time in milliseconds
     */
    executionTimeMs: number;
  };
}
