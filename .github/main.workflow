workflow "push docker image on tag" {
  resolves = [
    "filter version tag",
    "docker push latest",
    "docker push version",
    "docker push sha",
  ]
  on = "create"
}

action "filter version tag" {
  uses = "actions/bin/filter@master"
  args = "tag v*"
}

action "docker login" {
  uses = "actions/docker/login@master"
  secrets = [
    "DOCKER_PASSWORD",
    "DOCKER_USERNAME"]
}

action "docker build" {
  uses = "actions/docker/cli@master"
  needs = "docker login"
  args = "build --tag wamplius ."
}

action "docker tag" {
  uses = "actions/docker/tag@master"
  needs = "docker build"
  args = "--env wamplius gieseladev/wamplius"
}

action "docker push latest" {
  uses = "actions/docker/cli@master"
  needs = "docker tag"
  args = "push gieseladev/wamplius:latest"
}

action "docker push version" {
  uses = "actions/docker/cli@master"
  needs = "docker tag"
  args = "push gieseladev/wamplius:$IMAGE_REF"
}

action "docker push sha" {
  uses = "actions/docker/cli@master"
  needs = "docker tag"
  args = "push gieseladev/wamplius:$IMAGE_SHA"
}
