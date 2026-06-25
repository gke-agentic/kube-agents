resource "google_container_cluster" "prod_us_west1" {
  name     = "prod-us-west1"
  location = "us-west1"

  # Regional cluster configuration
  remove_default_node_pool = true
  initial_node_count       = 1

  network    = "default"
  subnetwork = "default"
}

resource "google_container_node_pool" "primary_nodes" {
  name       = "prod-us-west1-node-pool"
  cluster    = google_container_cluster.prod_us_west1.name
  location   = "us-west1"
  node_count = 3

  autoscaling {
    min_node_count = 3
    max_node_count = 10
  }

  node_config {
    machine_type = "e2-standard-4"
    disk_size_gb = 100
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}
