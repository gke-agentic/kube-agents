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

package controller

import (
	"context"
	"fmt"
	"reflect"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	devteamv1alpha1 "github.com/gke-agentic/devteam-agent-operator/api/v1alpha1"
)

// DevTeamAgentReconciler reconciles a DevTeamAgent object
type DevTeamAgentReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=devteam.platform.io,resources=devteamagents,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=devteam.platform.io,resources=devteamagents/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=devteam.platform.io,resources=devteamagents/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=persistentvolumeclaims;services;serviceaccounts,verbs=get;list;watch;create;update;patch;delete

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
func (r *DevTeamAgentReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// 1. Fetch the DevTeamAgent instance
	instance := &devteamv1alpha1.DevTeamAgent{}
	err := r.Get(ctx, req.NamespacedName, instance)
	if err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	log.Info("Reconciling DevTeamAgent", "name", instance.Name)

	// Update status phase to Provisioning if empty
	if instance.Status.Phase == "" {
		instance.Status.Phase = "Provisioning"
		if err := r.Status().Update(ctx, instance); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// 2. Reconcile ServiceAccount (Workload Identity)
	if err := r.reconcileKSA(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile ServiceAccount")
		return ctrl.Result{}, err
	}

	// 3. Reconcile PVC
	if err := r.reconcilePVC(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile PVC")
		return ctrl.Result{}, err
	}

	// 4. Reconcile Deployment
	if err := r.reconcileDeployment(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile Deployment")
		return ctrl.Result{}, err
	}

	// 5. Reconcile Service
	if err := r.reconcileService(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile Service")
		return ctrl.Result{}, err
	}

	// Update status phase to Ready
	if instance.Status.Phase != "Ready" {
		instance.Status.Phase = "Ready"
		if err := r.Status().Update(ctx, instance); err != nil {
			return ctrl.Result{}, err
		}
		log.Info("DevTeamAgent is Ready", "name", instance.Name)
	}

	return ctrl.Result{}, nil
}

func (r *DevTeamAgentReconciler) reconcileKSA(ctx context.Context, instance *devteamv1alpha1.DevTeamAgent) error {
	ksaName := instance.Spec.KSAName
	if ksaName == "" {
		ksaName = instance.Name
	}

	ksa := &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ksaName,
			Namespace: instance.Namespace,
		},
	}

	if instance.Spec.GSAName != "" && instance.Spec.ProjectID != "" {
		ksa.Annotations = map[string]string{
			"iam.gke.io/gcp-service-account": fmt.Sprintf("%s@%s.iam.gserviceaccount.com", instance.Spec.GSAName, instance.Spec.ProjectID),
		}
	}

	if err := ctrl.SetControllerReference(instance, ksa, r.Scheme); err != nil {
		return err
	}

	found := &corev1.ServiceAccount{}
	err := r.Get(ctx, client.ObjectKey{Name: ksa.Name, Namespace: ksa.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, ksa)
		}
		return err
	}

	if !reflect.DeepEqual(found.Annotations, ksa.Annotations) {
		found.Annotations = ksa.Annotations
		return r.Update(ctx, found)
	}

	return nil
}

func (r *DevTeamAgentReconciler) reconcilePVC(ctx context.Context, instance *devteamv1alpha1.DevTeamAgent) error {
	storageSize := instance.Spec.StorageSize
	if storageSize == "" {
		storageSize = "10Gi"
	}

	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      instance.Name + "-pvc",
			Namespace: instance.Namespace,
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

	if err := ctrl.SetControllerReference(instance, pvc, r.Scheme); err != nil {
		return err
	}

	found := &corev1.PersistentVolumeClaim{}
	err := r.Get(ctx, client.ObjectKey{Name: pvc.Name, Namespace: pvc.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, pvc)
		}
		return err
	}

	return nil
}

func (r *DevTeamAgentReconciler) reconcileDeployment(ctx context.Context, instance *devteamv1alpha1.DevTeamAgent) error {
	replicas := int32(1)
	if instance.Spec.Replicas != nil {
		replicas = *instance.Spec.Replicas
	}

	secretRef := instance.Spec.ApiServerKeySecretRef
	if secretRef == "" {
		secretRef = "devteam-agent-secrets"
	}

	ksaName := instance.Spec.KSAName
	if ksaName == "" {
		ksaName = instance.Name
	}

	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      instance.Name,
			Namespace: instance.Namespace,
			Labels: map[string]string{
				"app": instance.Name,
			},
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app": instance.Name,
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app": instance.Name,
					},
				},
				Spec: corev1.PodSpec{
					ServiceAccountName: ksaName,
					Containers: []corev1.Container{
						{
							Name:  "devteam-agent",
							Image: instance.Spec.ImageURI,
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
									Value: instance.Spec.ModelName,
								},
								{
									Name:  "MODEL_BASE_URL",
									Value: instance.Spec.ModelBaseURL,
								},
								{
									Name:  "MODEL_API_KEY",
									Value: instance.Spec.ModelAPIKey,
								},
								{
									Name:  "GKE_CLUSTER_NAME",
									Value: instance.Spec.ClusterName,
								},
								{
									Name:  "GKE_LOCATION",
									Value: instance.Spec.Location,
								},
								{
									Name:  "GCP_PROJECT_ID",
									Value: instance.Spec.ProjectID,
								},
								{
									Name:  "PROJECT_ID",
									Value: instance.Spec.ProjectID,
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
									ClaimName: instance.Name + "-pvc",
								},
							},
						},
					},
				},
			},
		},
	}

	if err := ctrl.SetControllerReference(instance, deploy, r.Scheme); err != nil {
		return err
	}

	found := &appsv1.Deployment{}
	err := r.Get(ctx, client.ObjectKey{Name: deploy.Name, Namespace: deploy.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, deploy)
		}
		return err
	}

	if !reflect.DeepEqual(found.Spec, deploy.Spec) || !reflect.DeepEqual(found.Labels, deploy.Labels) {
		found.Spec = deploy.Spec
		found.Labels = deploy.Labels
		return r.Update(ctx, found)
	}
	return nil
}

func (r *DevTeamAgentReconciler) reconcileService(ctx context.Context, instance *devteamv1alpha1.DevTeamAgent) error {
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      instance.Name,
			Namespace: instance.Namespace,
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{
				"app": instance.Name,
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

	if err := ctrl.SetControllerReference(instance, svc, r.Scheme); err != nil {
		return err
	}

	found := &corev1.Service{}
	err := r.Get(ctx, client.ObjectKey{Name: svc.Name, Namespace: svc.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, svc)
		}
		return err
	}

	if !reflect.DeepEqual(found.Spec.Ports, svc.Spec.Ports) || !reflect.DeepEqual(found.Spec.Selector, svc.Spec.Selector) {
		found.Spec.Ports = svc.Spec.Ports
		found.Spec.Selector = svc.Spec.Selector
		return r.Update(ctx, found)
	}
	return nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *DevTeamAgentReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&devteamv1alpha1.DevTeamAgent{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.PersistentVolumeClaim{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Named("devteamagent").
		Complete(r)
}
