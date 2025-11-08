import { usePathname } from 'next/navigation'
import Link from 'next/link'

export default function PhenoBreadcrumbs({ params }: { params: { speciesId: number } }) {
  return (
    <div className="text-xl mb-8 select-none">
      {/* <span className='text-stone-400'>
        <span className='hover:underline'><Link href='/app/phenotypes'>All species</Link></span>
        &nbsp;▸&nbsp;
        <span className='hover:underline capitalize'>
          <Link href={`/app/phenotypes/${species?.id}`}>
              {speciesName}
          </Link>
        </span>
        &nbsp;▸&nbsp;
        <span className='hover:underline'>
          <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}`}>
              {experimentName}
          </Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span className=''>
        Wave {waveNumber}
      </span> */}
    </div>
  )
}
