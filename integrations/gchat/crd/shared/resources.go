/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package shared

import (
	"context"
	"fmt"
	"maps"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

// BuildService builds a standard Service for the agent.
func BuildService(name, namespace string) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: namespace,
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{
				"app": name,
			},
			Ports: []corev1.ServicePort{
				{
					Name:       "api",
					Protocol:   corev1.ProtocolTCP,
					Port:       8642,
					TargetPort: intstr.FromInt(8642),
				},
				{
					Name:       "dashboard",
					Protocol:   corev1.ProtocolTCP,
					Port:       9119,
					TargetPort: intstr.FromInt(9119),
				},
			},
			Type: corev1.ServiceTypeClusterIP,
		},
	}
}

// BuildPVC builds a PersistentVolumeClaim for the agent.
func BuildPVC(pvcName, namespace, storageSize string) *corev1.PersistentVolumeClaim {
	if storageSize == "" {
		storageSize = "10Gi"
	}
	return &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      pvcName,
			Namespace: namespace,
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceStorage: resource.MustParse(storageSize),
				},
			},
		},
	}
}

// BuildKSA builds a Kubernetes ServiceAccount with optional Workload Identity annotation.
func BuildKSA(ksaName, namespace, gsaName, projectID string) *corev1.ServiceAccount {
	ksa := &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ksaName,
			Namespace: namespace,
		},
	}
	if gsaName != "" && projectID != "" {
		ksa.Annotations = map[string]string{
			"iam.gke.io/gcp-service-account": fmt.Sprintf("%s@%s.iam.gserviceaccount.com", gsaName, projectID),
		}
	}
	return ksa
}

// DeploymentConfig holds configuration for building an agent Deployment.
type DeploymentConfig struct {
	Name                  string
	Namespace             string
	ImageURI              string
	Replicas              *int32
	KSAName               string
	ContainerName         string
	ApiServerKeySecretRef string
	ModelName             string
	ModelBaseURL          string
	ModelAPIKey           string
	ClusterName           string
	Location              string
	ProjectID             string
	PVCName               string
}

// BuildDeployment builds a Deployment for Operator/DevTeam agents.
func BuildDeployment(cfg DeploymentConfig) *appsv1.Deployment {
	replicas := int32(1)
	if cfg.Replicas != nil {
		replicas = *cfg.Replicas
	}

	secretRef := cfg.ApiServerKeySecretRef
	if secretRef == "" {
		secretRef = cfg.ContainerName + "-secrets" // e.g. operator-agent-secrets
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      cfg.Name,
			Namespace: cfg.Namespace,
			Labels: map[string]string{
				"app": cfg.Name,
			},
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app": cfg.Name,
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app": cfg.Name,
					},
				},
				Spec: corev1.PodSpec{
					ServiceAccountName: cfg.KSAName,
					Containers: []corev1.Container{
						{
							Name:  cfg.ContainerName,
							Image: cfg.ImageURI,
							Ports: []corev1.ContainerPort{
								{
									Name:          "api",
									ContainerPort: 8642,
								},
								{
									Name:          "dashboard",
									ContainerPort: 9119,
								},
							},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("2"),
									corev1.ResourceMemory: resource.MustParse("2Gi"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("4"),
									corev1.ResourceMemory: resource.MustParse("4Gi"),
								},
							},
							Env: []corev1.EnvVar{
								{
									Name:  "API_SERVER_ENABLED",
									Value: "true",
								},
								{
									Name:  "HERMES_DASHBOARD",
									Value: "1",
								},
								{
									Name:  "API_SERVER_HOST",
									Value: "0.0.0.0",
								},
								{
									Name: "API_SERVER_KEY",
									ValueFrom: &corev1.EnvVarSource{
										SecretKeyRef: &corev1.SecretKeySelector{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: secretRef,
											},
											Key: "api-server-key",
										},
									},
								},
								{
									Name:  "MODEL_NAME",
									Value: cfg.ModelName,
								},
								{
									Name:  "MODEL_BASE_URL",
									Value: cfg.ModelBaseURL,
								},
								{
									Name:  "MODEL_API_KEY",
									Value: cfg.ModelAPIKey,
								},
								{
									Name:  "GKE_CLUSTER_NAME",
									Value: cfg.ClusterName,
								},
								{
									Name:  "GKE_LOCATION",
									Value: cfg.Location,
								},
								{
									Name:  "GCP_PROJECT_ID",
									Value: cfg.ProjectID,
								},
								{
									Name:  "PROJECT_ID",
									Value: cfg.ProjectID,
								},
							},
							VolumeMounts: []corev1.VolumeMount{
								{
									Name:      "data-volume",
									MountPath: "/opt/data",
								},
							},
						},
					},
					Volumes: []corev1.Volume{
						{
							Name: "data-volume",
							VolumeSource: corev1.VolumeSource{
								PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
									ClaimName: cfg.PVCName,
								},
							},
						},
					},
				},
			},
		},
	}
}

// IsMapSubset returns true if desired map is a subset of found map.
func IsMapSubset(desired, found map[string]any) bool {
	for k, desiredVal := range desired {
		foundVal, exists := found[k]
		if !exists {
			logf.Log.V(1).Info("IsMapSubset diff: Key missing in found", "key", k)
			return false
		}
		desiredMap, desiredIsMap := desiredVal.(map[string]any)
		foundMap, foundIsMap := foundVal.(map[string]any)

		if desiredIsMap && foundIsMap {
			if !IsMapSubset(desiredMap, foundMap) {
				return false
			}
		} else {
			desiredStr := fmt.Sprintf("%v", desiredVal)
			foundStr := fmt.Sprintf("%v", foundVal)
			if desiredStr != foundStr {
				logf.Log.V(1).Info("IsMapSubset diff: Values differ", "key", k, "desired", desiredStr, "found", foundStr)
				return false
			}
		}
	}
	return true
}

// CreateOrUpdateUnstructured merges desired unstructured object into found one.
// Returns requeue=true if immutable resource changed and was deleted.
func CreateOrUpdateUnstructured(ctx context.Context, c client.Client, obj *unstructured.Unstructured) (bool, error) {
	log := logf.FromContext(ctx)
	found := &unstructured.Unstructured{}
	found.SetGroupVersionKind(obj.GroupVersionKind())
	err := c.Get(ctx, client.ObjectKey{Name: obj.GetName(), Namespace: obj.GetNamespace()}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return false, c.Create(ctx, obj)
		}
		return false, err
	}

	// 1. Check if Spec has changed
	desiredSpec, desiredSpecExists, _ := unstructured.NestedMap(obj.Object, "spec")
	foundSpec, foundSpecExists, _ := unstructured.NestedMap(found.Object, "spec")

	specChanged := false
	if desiredSpecExists && foundSpecExists {
		if !IsMapSubset(desiredSpec, foundSpec) {
			specChanged = true
			// Spec differs! For immutable IAMPolicyMember, we MUST delete and request requeue.
			if obj.GetKind() == "IAMPolicyMember" {
				log.Info("Spec of immutable IAMPolicyMember changed. Deleting and requesting requeue...", "name", obj.GetName())
				if err := c.Delete(ctx, found); err != nil {
					return false, err
				}
				return true, nil
			}
		}
	}

	// If the spec did NOT change and this is an IAMPolicyMember, we MUST skip to avoid GKE webhook denials!
	if !specChanged && obj.GetKind() == "IAMPolicyMember" {
		return false, nil
	}

	desiredLabels := obj.GetLabels()
	foundLabels := found.GetLabels()
	labelsChanged := false
	for k, v := range desiredLabels {
		if foundLabels == nil || foundLabels[k] != v {
			labelsChanged = true
			break
		}
	}

	desiredAnnotations := obj.GetAnnotations()
	foundAnnotations := found.GetAnnotations()
	annotationsChanged := false
	for k, v := range desiredAnnotations {
		if foundAnnotations == nil || foundAnnotations[k] != v {
			annotationsChanged = true
			break
		}
	}

	if !specChanged && !labelsChanged && !annotationsChanged {
		return false, nil
	}

	// 2. Merge Spec (for mutable resources)
	if desiredSpecExists {
		if !foundSpecExists {
			foundSpec = make(map[string]any)
		}
		maps.Copy(foundSpec, desiredSpec)
		err = unstructured.SetNestedMap(found.Object, foundSpec, "spec")
		if err != nil {
			return false, err
		}
	}

	// 3. Merge Labels
	if foundLabels == nil {
		foundLabels = make(map[string]string)
	}
	maps.Copy(foundLabels, desiredLabels)
	found.SetLabels(foundLabels)

	// 4. Merge Annotations
	if foundAnnotations == nil {
		foundAnnotations = make(map[string]string)
	}
	maps.Copy(foundAnnotations, desiredAnnotations)
	found.SetAnnotations(foundAnnotations)

	return false, c.Update(ctx, found)
}
