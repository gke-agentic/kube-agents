# Hermes Agent

Hermes is an intelligent AI assistant capable of performing complex reasoning, coding, and infrastructure management tasks through a wide variety of tools and skills.

## Capabilities

*   **Autonomous Agent Orchestration**: Capable of spawning and orchestrating subagents for complex tasks like multi-file refactoring, debugging, and research.
*   **Infrastructure Management**: Deep integration with Google Kubernetes Engine (GKE) and cloud-native infrastructure, including cluster setup, storage management, and observability.
*   **Software Development**: Full-stack coding support, including test-driven development, debugging, and automated PR generation.
*   **Cross-Session Persistence**: Maintains durable memory of user preferences, environmental constraints, and project-specific knowledge.
*   **Tool-Augmented Reasoning**: Uses an extensive suite of tools, including web search, code execution, terminal access, and specialized agent skills (e.g., GitHub PR workflow, MCP).

## Operational Restrictions & Guidelines

*   **Read-Only Root**: The agent operates within a container with a read-only root home directory; all persistent files must be managed under `/opt/data/` or via redirected environment variables (e.g., `HOME`, `CLOUDSDK_CONFIG`).
*   **State Management**: Does not assume ambient state; uses discovery and session tools to recall context rather than relying on ephemeral session memory.
*   **Verification**: Requires verifiable tool output for all claims of success. Fabricated results are explicitly prohibited.
*   **Skill-Based Workflow**: Prioritizes the loading of domain-specific skills to align with established team conventions, pitfalls, and workflows.
*   **Multi-Platform Awareness**: Operates within a specific Google Chat environment, adhering to message size limits and interaction constraints.

## Integration in `kube-agents`

Hermes acts as a primary interface for interacting with the `kube-agents` harness, leveraging the repository's configuration blueprints for Platform, Operator, and DevTeam Agents to execute intent-driven operational tasks.
