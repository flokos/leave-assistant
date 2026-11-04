# Enterprise Leave Management AI Assistant
## Master Project Brief & Architectural Assignment

---

# 1. YOUR ROLE

Act as the Principal Software Architect, Senior AI Engineer and Technical Lead responsible for designing and guiding the development of this application.

You have substantial architectural autonomy.

Your responsibilities include:

- Understanding the product motivation and business requirements.
- Designing the overall system architecture.
- Evaluating the proposed technologies.
- Making technical decisions where requirements are sufficiently clear.
- Identifying architectural inconsistencies and unnecessary complexity.
- Designing secure agentic workflows.
- Defining implementation milestones.
- Maintaining comprehensive technical documentation.
- Ensuring that the resulting application is maintainable, testable and extensible.

We will use Claude Code exclusively as the software development assistant.

**Claude is not a runtime component of the resulting application.**

The runtime LLM providers will be Google Gemini and Groq.

We expect you to act as an engineering partner, not simply execute instructions mechanically.

You should challenge questionable decisions, explain trade-offs and recommend improvements. However, do not silently replace decisions that have already been explicitly established.

---

# 2. PROJECT MOTIVATION AND INITIAL INSPIRATION

Organizations accumulate considerable amounts of operational knowledge across documents, internal databases, business policies and information systems.

Although Large Language Models provide new ways of interacting with this information, connecting an LLM to a document collection is insufficient for building reliable enterprise applications.

An enterprise AI assistant must do more than answer questions.

It should understand business context, retrieve relevant information, interact with existing systems, respect access permissions, coordinate multi-step operations and provide traceability for everything it does.

This creates several interesting engineering challenges:

- Combining semantic document retrieval with structured operational data.
- Coordinating multi-step AI workflows.
- Integrating probabilistic LLM behaviour with deterministic backend services.
- Maintaining execution state across agent workflows.
- Implementing reliable tool execution and error recovery.
- Enforcing authorization independently of LLM instructions.
- Protecting confidential information.
- Preventing prompt injection and unauthorized actions.
- Supporting human oversight and auditable operations.
- Evaluating the reliability and business value of AI agents.

The initial inspiration is to explore how these challenges can be addressed through a combination of applied AI, backend engineering, workflow automation and enterprise architecture.

The resulting application should demonstrate how an AI agent can become a controlled interface between human users, enterprise knowledge and business operations.

**The central principle is that the LLM should coordinate intelligent interactions, while deterministic backend systems remain responsible for permissions, business rules and actual execution.**

This is an independent software engineering project. Do not reference any employer, recruitment process, job advertisement or interview in the documentation, application or repository.

---

# 3. PRODUCT DEFINITION

Build a multilingual, multi-tenant Enterprise Leave Management AI Assistant.

The application will allow employees to interact with their company's leave-management system through natural language.

For example:

"How many vacation days do I have remaining? Can I request five days off starting December 14?"

The AI agent should be able to:

1. Understand the request.
2. Retrieve relevant company leave policies.
3. Identify the authenticated employee.
4. Retrieve their current leave balance.
5. Calculate working days, excluding weekends and public holidays.
6. Validate the requested leave.
7. Prepare a leave request.
8. Ask the employee for confirmation.
9. Submit the request through a backend API.
10. Route it to the appropriate manager or HR administrator.
11. Execute the approved operation.
12. Synchronize the relevant calendars.
13. Notify the employee.
14. Record the complete operation history.

This must be an actual agentic application with functioning backend integrations, not simply a chatbot demonstrating RAG.

The application should support multiple companies, with strict isolation of their information.

We will initially seed two fictional companies but support dynamically creating additional companies.

---

# 4. DECISIONS ALREADY MADE

The following decisions have already been discussed and selected.

Treat these as the project's established direction.

Where you identify incompatibilities or significant technical problems, explain them and propose alternatives before changing the design.

## 4.1 Deployment and development environment

**Deployment: Local only.**

The application must run locally through Docker Compose.

Requirements:

- All application components run in Docker containers.
- Persistent storage through Docker volumes.
- Reproducible environment.
- No mandatory cloud infrastructure.
- No public application deployment.
- No paid hosting dependencies.

There will nevertheless be external API dependencies:

- Gemini API.
- Groq API.
- Google Calendar API when using real calendar integration.

These are intentional external integrations.

Use free API tiers whenever available, and avoid introducing paid services.

Claude Code is exclusively our development tool.

## 4.2 Runtime LLM providers

Selected providers:

**Primary:** Google Gemini API.

**Alternative:** Groq API.

Implement:

- Provider abstraction.
- Automatic failover.
- Bounded retries.
- Timeouts.
- Provider health tracking.
- Logging of provider selection.
- Configuration through environment variables.

Failover should handle rate limits and temporary provider failures.

Do not automatically treat invalid model outputs, security violations or authorization failures as reasons to switch providers.

Do not introduce Claude as a runtime dependency.

## 4.3 Backend architecture

Selected technologies:

- Python.
- FastAPI.
- PostgreSQL.
- SQLAlchemy.
- Pydantic.

The backend owns:

- Authentication integration.
- Authorization.
- Business logic.
- API contracts.
- Document retrieval permissions.
- Leave calculations.
- Request validation.
- Business operations.
- Audit logging.
- Integrations.

Keep clear boundaries between API, application services, domain logic and infrastructure.

## 4.4 Identity and authentication

Selected technology: **Keycloak**.

Use:

- OpenID Connect.
- OAuth2.
- JWT validation.
- Role-based authorization.
- Group and tenant-aware user context.

Keycloak will run in Docker.

Its database should be logically separate from application data, even if the same PostgreSQL service is used.

## 4.5 User roles

The final role model contains five roles:

1. Employee.
2. Manager.
3. HR Administrator.
4. Company Administrator.
5. Platform Administrator.

Responsibilities:

**Employee**
- Read permitted company policies.
- Access personal leave information.
- Submit and manage leave requests.
- View personal leave history.
- Receive notifications.

**Manager**
- Access employees within their department.
- View relevant leave balances and availability.
- Approve requests according to company policies.
- Cannot access confidential HR documents.

**HR Administrator**
- Manage HR-related documents and workflows.
- Access authorized employee information.
- Handle exceptional leave cases.
- Review restricted supporting documents.
- Participate in approval workflows.

**Company Administrator**
- Manage company configuration.
- Manage users and departments.
- Manage company policies and permissions.
- Manage general company documents.
- Review quarantined general company documents.

**Platform Administrator**
- Create and manage tenants.
- Manage platform-wide configuration.
- Manage global security policies.
- Cannot automatically access confidential tenant information.

Platform administration and company administration must remain separate security domains.

## 4.6 Multi-tenancy and authorization

Selected approach:

**Shared PostgreSQL database with PostgreSQL Row-Level Security.**

Requirements:

- Every company has a unique tenant ID.
- Tenant IDs propagate through application operations.
- PostgreSQL RLS provides database-level isolation.
- FastAPI independently enforces authorization.
- Document retrieval applies both tenant and document permissions.
- LangGraph execution receives trusted authenticated context.
- The LLM cannot choose or override its tenant identity.

Implement defence in depth.

Pay particular attention to connection pooling and transaction-scoped tenant context so that one request cannot inherit another request's security context.

Companies should be dynamically creatable.

Each company has its own:

- Departments.
- Employees.
- Leave policies.
- Approval rules.
- Holiday calendar.
- Document permissions.
- Retention configuration.
- Calendar integrations.

Initial seed data: two fictional companies.

## 4.7 Organizational hierarchy

Selected approach:

Department-based organization.

Each company contains departments.

Departments have assigned managers and employees.

Managers can access relevant information for their entire department, but confidential HR information remains restricted.

Individual manager assignments should be preserved where necessary for approval routing.

---

# 5. LEAVE MANAGEMENT BUSINESS LOGIC

The application is specifically a leave-management assistant.

Do not expand it into a general HR platform, expense management platform or procurement application.

## 5.1 Leave types

Support:

- Annual leave.
- Sick leave.
- Unpaid leave.

## 5.2 Leave functionality

Implement:

- Leave balance retrieval.
- Leave requests.
- Request modification.
- Request cancellation.
- Leave history.
- Public holiday handling.
- Working-day calculations.
- Request validation.
- Manager approvals.
- HR approvals when required.

## 5.3 Public holidays

Each company has a configurable public holiday calendar.

The application must:

- Exclude weekends.
- Exclude configured public holidays.
- Calculate working days correctly.
- Support different holiday calendars per tenant.

## 5.4 Leave balance ledger

We selected a ledger-based approach.

Do not store the employee's remaining balance as an independently mutable value without a transaction history.

Maintain ledger entries for:

- Annual entitlement grants.
- Approved leave deductions.
- Manual adjustments.
- Reversals of cancelled leave.
- Other authorized balance changes.

Balances should be derived from ledger entries.

All financial-style state changes must be transactional and auditable.

## 5.5 Approval rules

Approval policies must be configurable per company.

Initial demonstration rules:

- Up to five working days: Manager approval.
- More than five working days: Manager followed by HR approval.
- Sick leave: HR review when required.

These rules must reside in deterministic backend business logic.

Never rely on an LLM prompt to implement approval policies.

## 5.6 Supporting sick-leave documents

Employees may upload supporting documents.

Requirements:

- Stored in SeaweedFS.
- Accessible only to appropriately authorized HR personnel.
- Access events audited.
- No medical diagnosis extraction.
- Never send supporting medical documents or their contents to external LLM providers.
- Do not expose supporting document contents to Langfuse.

## 5.7 Leave retention policies

Each company has configurable document retention policies.

Requirements:

- Identify documents eligible for deletion.
- Notify appropriate HR personnel.
- Allow authorized HR review.
- Audit retention configuration and deletion.
- Do not assume fixed legal retention periods.

---

# 6. AGENT ARCHITECTURE

Selected framework: **LangGraph**.

We specifically chose a single orchestrating agent with modular subgraphs rather than an unrestricted collection of autonomous agents.

The anticipated subgraphs are:

### Knowledge Subgraph
- Document retrieval.
- Semantic search.
- Hybrid search.
- Reranking.
- Context construction.
- Source attribution.

### Business Operations Subgraph
- Employee information retrieval.
- Leave balance retrieval.
- Request preparation.
- Business-rule validation.
- Leave operations.

### Approval Subgraph
- Employee confirmation.
- Manager approval.
- HR approval.
- Authorization checks.
- Action execution.
- Workflow completion.

A central LangGraph orchestrator coordinates these components.

We want to demonstrate:

- StateGraph.
- Conditional routing.
- Typed graph state.
- Tool/function calling.
- Modular subgraphs.
- Checkpointing.
- Interrupts.
- Human-in-the-loop.
- Persistent execution.
- Error recovery.
- Controlled retries.

## 6.1 Persistence

Selected approach: LangGraph PostgreSQL Checkpointer.

Persist graph execution state in PostgreSQL.

Support:

- Unique workflow/thread IDs.
- Resuming interrupted workflows.
- Human approvals.
- Application restarts.
- Multi-step execution.
- Workflow history.

Keep graph state, conversational history and business records logically distinct.

## 6.2 Tool execution security

The LLM may propose tool calls.

The backend determines whether the operation is permitted.

Separate:

- Retrieving information.
- Preparing actions.
- Confirming actions.
- Executing actions.

The LLM must never directly invoke privileged business operations outside the controlled execution path.

Every operation must validate:

- Authenticated identity.
- Tenant.
- Role.
- Resource ownership.
- Business rules.
- Approval state.

Implement idempotency for every state-changing operation.

---

# 7. RAG AND DOCUMENT INTELLIGENCE

We have selected a fairly sophisticated RAG architecture.

## 7.1 Document formats

Support:

- PDF.
- DOCX.
- TXT.
- Markdown.

Also support scanned PDFs using OCR.

## 7.2 Document storage

Selected technology: **SeaweedFS**.

Use its S3-compatible interface.

Requirements:

- Persistent object storage.
- Document metadata in PostgreSQL.
- Separation between original documents and extracted content.
- Version and lifecycle management.
- Secure tenant-aware document access.

Introduce a storage abstraction so SeaweedFS is not hardcoded throughout the application.

## 7.3 Document processing

Selected technology: **Docling**.

Use it for document parsing and extraction.

Preserve, where possible:

- Headings.
- Sections.
- Tables.
- Document structure.
- Metadata.

Support OCR where necessary.

## 7.4 Chunking

Selected approach: Hybrid, structure-aware chunking.

Start with document structure and headings.

Then divide oversized sections according to token limits.

Preserve context and appropriate overlap.

Do not apply simplistic fixed-size chunking to every document without considering its structure.

## 7.5 Embeddings

Selected library: Sentence Transformers.

Initial model preference:

`intfloat/multilingual-e5-small`

Run embeddings locally.

Requirements:

- Greek support.
- English support.
- No hosted embedding API.
- Consistent embedding model for indexing and querying.
- Appropriate query and passage prefixes.
- Reproducible evaluation.

## 7.6 Vector databases

We explicitly selected both:

- ChromaDB.
- PostgreSQL with pgvector.

The purpose is to compare retrieval approaches.

Use the same document corpus, embedding model and evaluation dataset.

Do not unnecessarily maintain two fully independent production systems.

Propose an abstraction that allows the retrieval backend to be selected and evaluated.

The final default should be justified by actual evaluation results.

## 7.7 Retrieval strategy

Selected approach:

**Hybrid search + reranking.**

We want:

1. Vector similarity search.
2. Keyword-based retrieval.
3. Result fusion.
4. Local multilingual reranking.
5. Final context selection.

Select a suitable local multilingual reranker.

We have not predetermined its exact model.

Evaluate retrieval quality in Greek and English.

## 7.8 Strict grounding

Selected behaviour: Strict RAG grounding.

For company-specific questions:

- Use authorized retrieved documents or authoritative business data.
- Cite supporting sources.
- Do not invent policies.
- Do not fabricate employee information.
- Explain when evidence is insufficient.
- Ask for clarification when necessary.

The assistant must be comfortable saying that it does not know.

---

# 8. DOCUMENT AUTOMATION WITH N8N

Selected technology: **Self-hosted n8n Community Edition**.

n8n should handle:

- Document ingestion orchestration.
- Document update workflows.
- Re-indexing triggers.
- Document lifecycle automation.
- Scheduled checks.
- Notifications.
- Selected background operational workflows.

However, FastAPI and Python retain responsibility for:

- Document parsing.
- Chunking.
- Embedding generation.
- Security-critical processing.
- Permission-aware retrieval.
- Business logic.

n8n must not become an alternative agent orchestration framework.

LangGraph controls AI workflows.

n8n controls automation workflows.

Keep these responsibilities separate.

Respect n8n's licensing conditions for the intended self-hosted usage.

---

# 9. SECURITY AND PRIVACY

Security is one of the project's central objectives.

## 9.1 Personal information detection

Selected technology: Microsoft Presidio with custom recognizers.

We want to support general PII detection and custom Greek identifiers.

## 9.2 Pseudonymization

Selected approach: Pseudonymization.

For example:

Alice Smith -> EMPLOYEE_001

alice@company.com -> EMAIL_001

Maintain the mapping locally.

The LLM must never receive the mapping.

## 9.3 Bidirectional protection

PII protection must cover:

- Incoming user messages.
- Retrieved documents.
- Business tool responses.
- Conversation history.
- All outbound LLM requests.
- LLM-generated responses.

Re-identification happens locally and only when the authenticated user is authorized to see the original information.

Do not blindly restore all placeholders.

Sensitive document categories, particularly sick-leave supporting documents, must be excluded from hosted LLM processing entirely.

Presidio detection is not a guarantee that all personal data will be detected. Design fail-closed policies for sensitive information and do not rely on PII redaction as a substitute for authorization.

## 9.4 Prompt injection protection

Selected approach: Comprehensive protection.

Treat uploaded and retrieved content as untrusted.

Implement layered controls:

- Document inspection.
- Prompt injection detection.
- Input and context classification.
- Risk scoring.
- Tool allowlisting.
- Strict argument validation.
- Backend authorization.
- Controlled execution paths.
- Adversarial tests.

## 9.5 Risk-based document handling

Selected policy:

- Low risk: Accept with warning.
- Medium risk: Require administrator review.
- High risk: Quarantine automatically.

Security thresholds should be configurable per company.

Review responsibilities:

- HR Administrator: HR-related documents.
- Company Administrator: General company documents.
- Platform Administrator: Global security policy management, without automatic access to tenant document contents.

Quarantined documents must not be retrievable by the agent.

The exact document-upload permissions for each role have not yet been finalized. Propose a secure, practical permission model.

---

# 10. EXTERNAL INTEGRATIONS

## 10.1 Google Calendar

Selected integration: Google Calendar API.

We want both:

- Shared company leave calendars.
- Optional employee personal calendars.

Use OAuth2.

Each Company Administrator should be able to authorize the company's shared calendar.

Employees may optionally connect personal calendars.

Calendar events should expose only:

- Employee name.
- Absence dates.
- General availability status.

Do not expose leave type, medical information or supporting documents.

## 10.2 Calendar reliability

Selected approach: Automatic synchronization with manual reconciliation.

Support:

- Event creation after final approval.
- Event modification.
- Event cancellation.
- Retry handling.
- Idempotency.
- Synchronization history.
- Failed synchronization tracking.
- Manual reconciliation.

Implement both:

- Real Google Calendar adapter.
- Local mock Calendar adapter.

The mock must be usable for automated testing without external credentials.

## 10.3 Notifications

Selected approach: Both in-app notifications and simulated email.

Use:

- PostgreSQL for notification records.
- n8n for notification workflows.
- MailHog for local email capture.

Do not require a real external email provider.

---

# 11. FRONTEND

Selected technology: **React + TypeScript**.

We chose this instead of Streamlit.

The application should have a professional enterprise interface.

Expected areas:

### Employee workspace
- Chat assistant.
- Document citations.
- Personal leave balance.
- Leave requests.
- Leave history.
- Notifications.
- Calendar connection.

### Manager workspace
- Department overview.
- Team availability.
- Pending approvals.
- Leave request details.

### HR workspace
- HR document management.
- Supporting document access.
- HR approvals.
- Retention review.
- Exceptional cases.

### Company administration
- Employee management.
- Department management.
- Roles and permissions.
- Company policies.
- Public holidays.
- Approval configuration.
- Document management.
- Calendar configuration.

### Platform administration
- Company creation.
- Tenant management.
- Platform configuration.
- Security policy management.

The interface must support Greek and English.

A user should be able to ask questions in either language and receive responses in the same language.

The application must not contain business logic that belongs in FastAPI.

---

# 12. OBSERVABILITY AND EVALUATION

## 12.1 Langfuse

Selected technology: Self-hosted Langfuse.

Use it for:

- LLM traces.
- Agent execution traces.
- Tool calls.
- Prompt inspection.
- Latency.
- Token usage.
- Evaluation experiments.

Do not store sensitive personal information or sick-leave document contents in Langfuse.

## 12.2 Automated testing

Selected approach:

**Pytest + RAGAS + Langfuse.**

Responsibilities:

Pytest:
- Unit testing.
- Integration testing.
- Authorization.
- Tenant isolation.
- Idempotency.
- Workflow correctness.
- Security regression tests.

RAGAS:
- Retrieval relevance.
- Answer relevance.
- Faithfulness.
- RAG quality comparisons.

Langfuse:
- Tracing.
- Experiment tracking.
- Evaluation history.

Evaluation must distinguish deterministic software correctness from probabilistic LLM quality.

Create an initial evaluation dataset of approximately 25–30 scenarios.

Include:

- Greek and English queries.
- Correct retrieval.
- Incorrect retrieval.
- Insufficient evidence.
- Tool selection.
- Multi-step execution.
- Unauthorized operations.
- Prompt injection.
- Duplicate actions.
- Retry scenarios.
- Approval workflows.
- Cross-tenant access attempts.

All security-critical tests should be executable without paid LLM API calls by using mocked model responses.

---

# 13. DOCUMENTATION IS A FIRST-CLASS DELIVERABLE

This is particularly important to us.

We do not want a repository containing only application code and a superficial README.

We want another software engineer to be able to understand how and why the solution was created.

Documentation must explain:

## Project motivation
- Initial inspiration.
- Problem statement.
- Business requirements.
- Intended capabilities.
- Scope and limitations.

## Architecture
- High-level architecture.
- Component responsibilities.
- System boundaries.
- Data flows.
- Deployment architecture.
- LangGraph architecture.
- RAG architecture.
- Security architecture.
- Integration architecture.

## Technology decisions
- Why each technology was selected.
- Alternatives considered.
- Advantages and disadvantages.
- Operational implications.

## Development
- Development methodology.
- Implementation roadmap.
- Milestones.
- AI-assisted development process.
- Testing methodology.
- Problems encountered.
- Architectural changes.

## Operations
- Installation.
- Docker Compose configuration.
- Environment variables.
- Database initialization.
- Seed data.
- Document ingestion.
- Running tests.
- Troubleshooting.

## Evaluation
- Evaluation datasets.
- Evaluation methodology.
- Actual results.
- Security assessment.
- Known weaknesses.
- Future improvements.

Use Mermaid diagrams for architecture and workflow visualizations.

Maintain Architecture Decision Records (ADRs).

Keep documentation synchronized with the implementation.

Never fabricate completed work, test results or performance claims.

The documentation must not mention any employer or recruitment context.

---

# 14. DEVELOPMENT WORKFLOW

We intend to use Claude Code in an architecture-first development process.

Our preferred methodology is:

1. Understand the problem.
2. Decompose requirements.
3. Discuss and review architecture.
4. Produce architecture documentation.
5. Break the implementation into smaller milestones.
6. Create actionable development tasks.
7. Implement incrementally.
8. Generate and execute tests.
9. Review the implementation.
10. Debug and improve.
11. Update documentation.
12. Evaluate the resulting system.

Use tests early, including TDD where appropriate.

We value:

- Small implementation milestones.
- Clear interfaces.
- Maintainable code.
- Explicit assumptions.
- Automated regression tests.
- Documented decisions.
- Technical ownership.

AI-assisted development should accelerate engineering work, not remove engineering accountability.

---

# 15. THE ARCHITECTURAL ASSIGNMENT

We have deliberately established the product requirements and many technology preferences.

We now want you to take ownership of the remaining architectural and implementation decisions.

Do not ask us to choose between every framework, library, model or implementation detail.

For areas where we have not specified a concrete implementation, investigate and recommend an appropriate technical solution.

Specifically, we want you to:

1. Review the complete project requirements.
2. Understand the original motivation.
3. Identify inconsistencies in our decisions.
4. Identify unnecessary complexity.
5. Evaluate compatibility between selected technologies.
6. Check current software versions and licensing.
7. Identify infrastructure and resource requirements.
8. Design the complete architecture.
9. Define the database schema.
10. Design the LangGraph workflows.
11. Design the RAG architecture.
12. Design n8n workflows.
13. Define the security model.
14. Define the API contracts.
15. Design the Google Calendar integration.
16. Design the document lifecycle.
17. Define observability and evaluation.
18. Design the frontend structure.
19. Produce implementation milestones.
20. Identify remaining decisions that genuinely require human input.

## Architectural autonomy

We want you to make decisions independently where appropriate.

For every significant technical decision, document:

- Context.
- Problem.
- Recommended solution.
- Alternatives.
- Advantages.
- Disadvantages.
- Security implications.
- Operational complexity.
- Consequences.

Use ADRs.

Do not introduce technology simply because it is popular or available.

Every component should have a clearly defined responsibility.

Avoid duplicating capabilities between LangGraph, n8n, FastAPI and other frameworks.

Prefer simpler solutions when they satisfy the requirements.

Do not introduce unnecessary microservices, distributed infrastructure or multiple autonomous agents.

## Important clarification

The list of selected technologies represents our preferred direction.

You may challenge a technical selection when compatibility, licensing, security, maintenance or architectural considerations justify doing so.

However:

- Do not silently change selected technologies.
- Do not remove required capabilities.
- Do not simplify away multi-tenancy or security.
- Do not replace LangGraph with a basic chatbot loop.
- Do not turn n8n into the agent orchestrator.
- Do not let the LLM own business authorization.
- Do not expand the application beyond leave management without a compelling reason.

Explain the problem and propose a resolution.

---

# 16. WHAT WE EXPECT FIRST

**DO NOT START IMPLEMENTING THE APPLICATION YET.**

Your first assignment is architecture and planning.

Produce:

1. Executive summary.
2. Refined requirements specification.
3. Functional and non-functional requirements.
4. Final technology stack.
5. High-level system architecture.
6. Component architecture.
7. Deployment architecture.
8. LangGraph workflow diagrams.
9. RAG and ingestion diagrams.
10. n8n workflow diagrams.
11. Database schema and relationships.
12. Multi-tenant security design.
13. Authentication and authorization design.
14. API contracts.
15. PII and prompt injection threat model.
16. Google Calendar integration design.
17. Testing and evaluation strategy.
18. Documentation structure.
19. Architecture Decision Records.
20. Implementation roadmap.
21. Risk register.
22. Unresolved questions requiring our input.

Use Mermaid diagrams where appropriate.

The architecture should be detailed enough that an independent engineering team could implement the system without having to reconstruct the original discussions.

At the end, provide:

- Which decisions you accepted unchanged.
- Which decisions you recommend modifying.
- Why those changes are justified.
- Which requirements remain ambiguous.
- Which assumptions you made.

Only ask us questions when the answer materially affects business functionality, user experience, security, privacy, project scope or budget.

For routine technical decisions, make a justified recommendation and document it.

---

# 17. FINAL PROJECT OBJECTIVE

The final result should be a fully functional, locally runnable, documented enterprise AI application.

It should demonstrate how multiple technologies can work together to create an intelligent but controlled enterprise system.

The application should provide a complete workflow from natural-language interaction to document retrieval, business-rule validation, human approval and execution through external systems.

The engineering emphasis is on the integration of:

- Applied AI.
- LLM engineering.
- Agent orchestration.
- RAG.
- Backend architecture.
- Enterprise integration.
- Security.
- Reliability.
- Evaluation.
- Observability.

We want to understand and be able to defend every important technical decision.

**Build an engineering project that demonstrates the ability to turn AI models into reliable, secure and maintainable enterprise software.**


How I suggest we use this
Give Claude Code this entire brief in the project root, ideally as PROJECT_BRIEF.md, and ask it to conduct the architecture assignment.

One important thing: don't let it immediately generate the application. First, review its proposed architecture, especially its decisions concerning PostgreSQL RLS, LangGraph persistence, PII protection, document security and the coexistence of ChromaDB and pgvector.

Those are the areas where a seemingly reasonable architecture can hide significant implementation problems.

The goal is for Claude to inherit our product thinking and make the remaining engineering decisions—not to restart the design process from scratch.