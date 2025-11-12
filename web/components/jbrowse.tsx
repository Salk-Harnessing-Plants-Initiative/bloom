'use client'

import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { createViewState, JBrowseApp } from '@jbrowse/react-app'
import { getEnv } from '@jbrowse/core/util'
import config from './config' // JBrowse configuration

type ViewModel = ReturnType<typeof createViewState>

function JBrowse() {
  const searchParams = useSearchParams()
  const reference = searchParams.get('reference') // Reference from URL
  const gene = searchParams.get('gene') // Gene from URL

  const [viewState, setViewState] = useState<ViewModel>()
  const [stateSnapshot, setStateSnapshot] = useState('')

  useEffect(() => {
    console.log(config)
    const state = createViewState({
      config: config,
    })
    const { pluginManager } = getEnv(state)
    setViewState(state)

    if (reference && gene) {
      pluginManager.evaluateAsyncExtensionPoint('LaunchView-LinearGenomeView', {
        tracks: [],
        loc: gene,
        assembly: reference,
        session: state.session,
      })
    }
  }, [])

  if (!viewState) {
    return null
  }

  return (
    <div className="p-4">
      <JBrowseApp viewState={viewState} />
    </div>
  )
}

export default JBrowse
