package tui

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/gke-agentic/kube-agents/cli/pkg/types"
	"gopkg.in/yaml.v3"
)

type step int

const (
	stepName step = iota
	stepPersona
	stepSkills
	stepNamespaces
	stepWorkflowMode
	stepDone
)

type studioModel struct {
	step         step
	agentName    string
	personaIndex int
	skillChoices map[int]bool
	cursor       int
	namespaces   string
	workflowMode int

	personas      []string
	skills        []string
	workflowModes []string

	manifestYAML string
	err          error
}

func initialStudioModel(defaultName string) studioModel {
	if defaultName == "" {
		defaultName = "platform-custodian"
	}
	return studioModel{
		step:          stepPersona,
		agentName:     defaultName,
		personaIndex:  0,
		skillChoices:  map[int]bool{0: true},
		namespaces:    "*",
		workflowMode:  0,
		personas:      []string{"standard-operator", "security-auditor", "incident-responder"},
		skills:        []string{"gke-observability", "k8s-remediation", "cost-optimizer"},
		workflowModes: []string{"Hybrid", "GitOps", "Direct"},
	}
}

func (m studioModel) Init() tea.Cmd {
	return nil
}

func (m studioModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c", "q":
			return m, tea.Quit
		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
			}
		case "down", "j":
			switch m.step {
			case stepPersona:
				if m.cursor < len(m.personas)-1 {
					m.cursor++
				}
			case stepSkills:
				if m.cursor < len(m.skills)-1 {
					m.cursor++
				}
			case stepWorkflowMode:
				if m.cursor < len(m.workflowModes)-1 {
					m.cursor++
				}
			}
		case " ":
			if m.step == stepSkills {
				m.skillChoices[m.cursor] = !m.skillChoices[m.cursor]
			}
		case "enter":
			switch m.step {
			case stepPersona:
				m.personaIndex = m.cursor
				m.cursor = 0
				m.step = stepSkills
			case stepSkills:
				m.cursor = 0
				m.step = stepWorkflowMode
			case stepWorkflowMode:
				m.workflowMode = m.cursor
				m.step = stepDone
				m.manifestYAML = m.GenerateYAML()
				return m, tea.Quit
			}
		}
	}
	return m, nil
}

func (m studioModel) View() string {
	if m.err != nil {
		return fmt.Sprintf("Error: %v\n", m.err)
	}

	var s strings.Builder
	switch m.step {
	case stepPersona:
		s.WriteString("Select Persona Archetype:\n\n")
		for i, p := range m.personas {
			cursor := " "
			if m.cursor == i {
				cursor = ">"
			}
			s.WriteString(fmt.Sprintf("%s [%d] %s\n", cursor, i+1, p))
		}
		s.WriteString("\nPress Enter to select, q to quit.\n")
	case stepSkills:
		s.WriteString("Select Skills (Space to toggle, Enter to continue):\n\n")
		for i, sk := range m.skills {
			cursor := " "
			if m.cursor == i {
				cursor = ">"
			}
			checked := " "
			if m.skillChoices[i] {
				checked = "x"
			}
			s.WriteString(fmt.Sprintf("%s [%s] %s\n", cursor, checked, sk))
		}
		s.WriteString("\nPress Enter when done.\n")
	case stepWorkflowMode:
		s.WriteString("Select Workflow Mode:\n\n")
		for i, wm := range m.workflowModes {
			cursor := " "
			if m.cursor == i {
				cursor = ">"
			}
			s.WriteString(fmt.Sprintf("%s %s\n", cursor, wm))
		}
		s.WriteString("\nPress Enter to finish.\n")
	case stepDone:
		s.WriteString("Generated KubeAgent Manifest:\n\n")
		s.WriteString(m.manifestYAML)
		s.WriteString("\nDone!\n")
	}
	return s.String()
}

// GenerateYAML produces the unified KubeAgent YAML manifest.
func (m studioModel) GenerateYAML() string {
	agent := GenerateAgentProfile(
		m.agentName,
		m.personas[m.personaIndex],
		m.getSelectedSkills(),
		strings.Split(m.namespaces, ","),
		m.workflowModes[m.workflowMode],
	)

	out, err := yaml.Marshal(agent)
	if err != nil {
		return fmt.Sprintf("# Error generating YAML: %v", err)
	}
	return string(out)
}

func (m studioModel) getSelectedSkills() []string {
	var selected []string
	for idx, sk := range m.skills {
		if m.skillChoices[idx] {
			selected = append(selected, sk)
		}
	}
	return selected
}

// GenerateAgentProfile constructs a KubeAgent blueprint struct deterministically.
func GenerateAgentProfile(agentName, persona string, skills []string, namespaces []string, workflowMode string) *types.KubeAgent {
	var skillRefs []types.SkillReference
	for _, sk := range skills {
		skillRefs = append(skillRefs, types.SkillReference{
			Name: sk,
			Ref:  fmt.Sprintf("./skills/%s", sk),
		})
	}

	return &types.KubeAgent{
		APIVersion: "kubeagents.x-k8s.io/v1alpha1",
		Kind:       "KubeAgent",
		Metadata: types.Metadata{
			Name:      agentName,
			Namespace: "kubeagents-system",
		},
		Spec: types.KubeAgentSpec{
			Harness: &types.HarnessSpec{
				ClusterName: "gke-production-us-central1",
				Location:    "us-central1",
			},
			PersonaRef: &types.PersonaReference{
				Name: persona,
				Ref:  fmt.Sprintf("./personas/%s/SOUL.md", persona),
			},
			Skills:       skillRefs,
			Namespaces:   namespaces,
			WorkflowMode: workflowMode,
		},
	}
}

// RunStudio executes the interactive TUI studio or generates directly if nonInteractive=true.
func RunStudio(outputPath string, nonInteractive bool, agentName, persona string, skills []string, namespaces []string, workflowMode string) error {
	if nonInteractive {
		if agentName == "" {
			agentName = "platform-custodian"
		}
		if persona == "" {
			persona = "standard-operator"
		}
		if len(skills) == 0 {
			skills = []string{"gke-observability"}
		}
		if len(namespaces) == 0 {
			namespaces = []string{"*"}
		}
		if workflowMode == "" {
			workflowMode = "Hybrid"
		}

		agent := GenerateAgentProfile(agentName, persona, skills, namespaces, workflowMode)
		data, err := yaml.Marshal(agent)
		if err != nil {
			return fmt.Errorf("failed to marshal KubeAgent manifest: %w", err)
		}

		if outputPath == "" || outputPath == "-" {
			_, err = os.Stdout.Write(data)
			return err
		}
		return os.WriteFile(outputPath, data, 0644)
	}

	model := initialStudioModel(agentName)
	p := tea.NewProgram(model)
	finalModel, err := p.Run()
	if err != nil {
		return fmt.Errorf("failed running TUI studio: %w", err)
	}

	sm, ok := finalModel.(studioModel)
	if !ok {
		return fmt.Errorf("unexpected TUI model type")
	}

	manifest := sm.GenerateYAML()
	if outputPath == "" || outputPath == "-" {
		fmt.Println(manifest)
		return nil
	}
	return os.WriteFile(outputPath, []byte(manifest), 0644)
}
