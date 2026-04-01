'use client'

import { useState, useEffect } from 'react'

type GitlabProject = {
  id: number
  description: string
  name: string
  name_with_namespace: string
  path: string
  path_with_namespace: string
  created_at: string
  default_branch: string
  tag_list: string[]
  topics: string[]
  ssh_url_to_repo: string
  http_url_to_repo: string
  web_url: string
  avatar_url: string
  star_count: number
  last_activity_at: string
  namespace: {
    id: number
    name: string
    path: string
    kind: string
    full_path: string
    parent_id: number
    avatar_url: string
    web_url: string
  }
}

export default function Pipelines() {
  const [loginStatusReady, setLoginStatusReady] = useState(false)
  const [loggedIn, setLoggedIn] = useState(false)
  const [projects, setProjects] = useState<GitlabProject[]>([])

  // check if logged in
  useEffect(() => {
    const checkLoggedIn = async () => {
      const response = await fetch('/api/gitlab/logged-in', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      })
      const data = await response.json()
      if (data.logged_in) {
        setLoggedIn(true)
      }
      setLoginStatusReady(true)
    }
    checkLoggedIn()
  }, [])

  // get projects
  useEffect(() => {
    const getProjects = async () => {
      const response = await fetch('/api/gitlab/projects', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      })
      const data = await response.json()
      setProjects(data)
    }
    getProjects()
  }, [])

  const initiateGitlabFlow = async () => {
    const response = await fetch('/api/oauth/gitlab/initiate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    })
    const data = await response.json()
    window.location.href = data.authorization_url
  }

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Pipelines</div>
      <div className="mb-6 select-none">Add computational pipelines via Gitlab or Github.</div>
      {loginStatusReady ? (
        loggedIn ? (
          <div className="pr-8">
            {/* list projects */}
            {projects.map((project) => (
              <div key={project.id} className="border border-neutral-700 rounded-md p-4 mb-4">
                <div className="flex justify-between items-center mb-2">
                  <div className="text-lg font-semibold">{project.name_with_namespace}</div>
                  <div className="text-sm text-neutral-500">{project.namespace.name}</div>
                </div>
                <div className="text-sm text-neutral-500 mb-2">{project.description}</div>
                <div className="text-sm text-neutral-500">{project.web_url}</div>
              </div>
            ))}
          </div>
        ) : (
          <button
            className="rounded-md py-2 px-4 border border-neutral-700 bg-violet-900 text-white mb-4"
            onClick={initiateGitlabFlow}
          >
            <img src="/gitlab-logo-500.svg" width={40} className="inline -my-2 -ml-2" />
            <span>Connect to Gitlab</span>
          </button>
        )
      ) : (
        <div>Loading...</div>
      )}
    </div>
  )
}
