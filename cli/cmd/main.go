package main

import (
	"fmt"
	"os"

	"github.com/gke-agentic/kube-agents/cli/pkg/compiler"
	"github.com/gke-agentic/kube-agents/cli/pkg/tui"
	"github.com/spf13/cobra"
)

func main() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var rootCmd = &cobra.Command{
	Use:   "kube-agent-cli",
	Short: "kube-agent-cli manages GKE Agent Blueprints and DASP Transpilation",
}

var (
	studioOutput         string
	studioNonInteractive bool
	studioAgentName      string
	studioPersona        string
	studioSkills         []string
	studioNamespaces     []string
	studioWorkflowMode   string
)

var studioCmd = &cobra.Command{
	Use:   "studio",
	Short: "Interactive TUI studio to assemble and generate KubeAgent manifests",
	RunE: func(cmd *cobra.Command, args []string) error {
		return tui.RunStudio(
			studioOutput,
			studioNonInteractive,
			studioAgentName,
			studioPersona,
			studioSkills,
			studioNamespaces,
			studioWorkflowMode,
		)
	},
}

var (
	compileProfile string
	compileOutput  string
	compileTarget  string
)

var compileCmd = &cobra.Command{
	Use:   "compile",
	Short: "Headless DASP transpiler for multi-harness targets (hermes, antigravity, scion, cloud-agents-api)",
	RunE: func(cmd *cobra.Command, args []string) error {
		if compileProfile == "" {
			return fmt.Errorf("--profile is required")
		}
		if compileOutput == "" {
			return fmt.Errorf("--output is required")
		}
		if compileTarget == "" {
			return fmt.Errorf("--target is required (hermes, antigravity, scion, cloud-agents-api)")
		}
		comp := compiler.NewCompiler()
		return comp.Compile(compileProfile, compileOutput, compileTarget)
	},
}

var (
	approveProposalsFile string
	approveNonInteractive bool
	approveAutoID        string
)

var approveCmd = &cobra.Command{
	Use:   "approve [agent-name]",
	Short: "Status monitor & approver for WAITING_FOR_APPROVAL proposals",
	Args:  cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentName := "all"
		if len(args) > 0 {
			agentName = args[0]
		}
		_ = agentName
		return tui.RunApprove(approveProposalsFile, approveNonInteractive, approveAutoID)
	},
}

func init() {
	studioCmd.Flags().StringVarP(&studioOutput, "output", "o", "agent-profile.yaml", "Output file path (- for stdout)")
	studioCmd.Flags().BoolVar(&studioNonInteractive, "non-interactive", false, "Generate deterministically without interactive TUI prompts")
	studioCmd.Flags().StringVar(&studioAgentName, "name", "platform-custodian", "Agent name")
	studioCmd.Flags().StringVar(&studioPersona, "persona", "standard-operator", "Persona archetype name")
	studioCmd.Flags().StringSliceVar(&studioSkills, "skills", []string{"gke-observability"}, "Skills to attach")
	studioCmd.Flags().StringSliceVar(&studioNamespaces, "namespaces", []string{"*"}, "Target namespaces")
	studioCmd.Flags().StringVar(&studioWorkflowMode, "workflow-mode", "Hybrid", "Workflow mode (Hybrid, GitOps, Direct)")

	compileCmd.Flags().StringVarP(&compileProfile, "profile", "p", "", "Path to input agent-profile.yaml blueprint")
	compileCmd.Flags().StringVarP(&compileOutput, "output", "o", "", "Path to output directory")
	compileCmd.Flags().StringVarP(&compileTarget, "target", "t", "hermes", "Target harness (hermes, antigravity, scion, cloud-agents-api)")

	approveCmd.Flags().StringVarP(&approveProposalsFile, "proposals", "p", "proposals.yaml", "Path to proposals YAML file")
	approveCmd.Flags().BoolVar(&approveNonInteractive, "non-interactive", false, "Run in non-interactive mode")
	approveCmd.Flags().StringVar(&approveAutoID, "approve-id", "", "Proposal ID to approve non-interactively")

	rootCmd.AddCommand(studioCmd)
	rootCmd.AddCommand(compileCmd)
	rootCmd.AddCommand(approveCmd)
}
