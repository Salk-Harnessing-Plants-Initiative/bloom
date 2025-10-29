
import ExperimentClient from './experimentclient'

export default async function ExperimentPage({
  params,
}: {
  params: Promise<{ experimentId: string }>
}) {
  const { experimentId } = await params
  return <ExperimentClient experimentId={Number(experimentId)} />
}

