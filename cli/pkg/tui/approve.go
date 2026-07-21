package tui

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/gke-agentic/kube-agents/cli/pkg/types"
	"gopkg.in/yaml.v3"
)

type approvalStep int

const (
	approvalList approvalStep = iota
	approvalDetail
	approvalDone
)

type approvalModel struct {
	proposals []types.Proposal
	cursor    int
	step      approvalStep
	action    string
	err       error
}

func initialApprovalModel(proposals []types.Proposal) approvalModel {
	return approvalModel{
		proposals: proposals,
		cursor:    0,
		step:      approvalList,
	}
}

func (m approvalModel) Init() tea.Cmd {
	return nil
}

func (m approvalModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c", "q":
			return m, tea.Quit
		case "up", "k":
			if m.step == approvalList && m.cursor > 0 {
				m.cursor--
			}
		case "down", "j":
			if m.step == approvalList && m.cursor < len(m.proposals)-1 {
				m.cursor++
			}
		case "enter":
			if m.step == approvalList && len(m.proposals) > 0 {
				m.step = approvalDetail
			}
		case "y", "a":
			if m.step == approvalDetail {
				m.proposals[m.cursor].Status = "APPROVED"
				m.action = "APPROVED"
				m.step = approvalDone
				return m, tea.Quit
			}
		case "n", "r":
			if m.step == approvalDetail {
				m.proposals[m.cursor].Status = "REJECTED"
				m.action = "REJECTED"
				m.step = approvalDone
				return m, tea.Quit
			}
		}
	}
	return m, nil
}

func (m approvalModel) View() string {
	if m.err != nil {
		return fmt.Sprintf("Error: %v\n", m.err)
	}

	var s strings.Builder
	switch m.step {
	case approvalList:
		s.WriteString("Pending WAITING_FOR_APPROVAL Proposals:\n\n")
		if len(m.proposals) == 0 {
			s.WriteString("No pending proposals found.\n")
			return s.String()
		}
		for i, prop := range m.proposals {
			cursor := " "
			if m.cursor == i {
				cursor = ">"
			}
			s.WriteString(fmt.Sprintf("%s [%s] Agent: %s - Summary: %s\n", cursor, prop.ID, prop.AgentName, prop.Summary))
		}
		s.WriteString("\nPress Enter to view diff, q to quit.\n")

	case approvalDetail:
		prop := m.proposals[m.cursor]
		s.WriteString(fmt.Sprintf("Proposal Detail [%s] for Agent %s\n", prop.ID, prop.AgentName))
		s.WriteString(fmt.Sprintf("Summary: %s\n\n", prop.Summary))
		s.WriteString("--- DIFF ---\n")
		s.WriteString(prop.Diff)
		s.WriteString("\n------------\n")
		s.WriteString("Press [y/a] to Approve, [n/r] to Reject, q to back out.\n")

	case approvalDone:
		s.WriteString(fmt.Sprintf("Proposal [%s] has been %s.\n", m.proposals[m.cursor].ID, m.action))
	}

	return s.String()
}

// LoadProposals loads pending proposals from a YAML file.
func LoadProposals(filePath string) ([]types.Proposal, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, err
	}

	var proposals []types.Proposal
	if err := yaml.Unmarshal(data, &proposals); err != nil {
		return nil, err
	}
	return proposals, nil
}

// SaveProposals writes proposals to a YAML file.
func SaveProposals(filePath string, proposals []types.Proposal) error {
	data, err := yaml.Marshal(proposals)
	if err != nil {
		return err
	}
	return os.WriteFile(filePath, data, 0644)
}

// RunApprove runs the interactive approval TUI or auto-approves/lists non-interactively.
func RunApprove(proposalsFile string, nonInteractive bool, autoApproveID string) error {
	proposals, err := LoadProposals(proposalsFile)
	if err != nil {
		if os.IsNotExist(err) {
			proposals = []types.Proposal{
				{
					ID:        "prop-101",
					AgentName: "platform-custodian",
					Status:    "WAITING_FOR_APPROVAL",
					Summary:   "Scale up nodes for platform-custodian agent",
					Diff:      "+ replicas: 3\n- replicas: 1",
				},
			}
		} else {
			return err
		}
	}

	if nonInteractive {
		if autoApproveID != "" {
			for i := range proposals {
				if proposals[i].ID == autoApproveID {
					proposals[i].Status = "APPROVED"
					fmt.Printf("Proposal %s marked as APPROVED\n", autoApproveID)
				}
			}
			return SaveProposals(proposalsFile, proposals)
		}
		for _, prop := range proposals {
			fmt.Printf("[%s] Agent: %s Status: %s Summary: %s\nDiff:\n%s\n", prop.ID, prop.AgentName, prop.Status, prop.Summary, prop.Diff)
		}
		return nil
	}

	model := initialApprovalModel(proposals)
	p := tea.NewProgram(model)
	finalModel, err := p.Run()
	if err != nil {
		return fmt.Errorf("failed running approval TUI: %w", err)
	}

	am, ok := finalModel.(approvalModel)
	if !ok {
		return fmt.Errorf("unexpected approval TUI model type")
	}

	return SaveProposals(proposalsFile, am.proposals)
}
