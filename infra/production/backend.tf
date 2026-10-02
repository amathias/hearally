terraform {
  backend "s3" {
    bucket       = "holds-terraform-state-files"
    key          = "hearally/prod.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
