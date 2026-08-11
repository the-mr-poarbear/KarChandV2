import Logo from "@/public/logo.svg"

const Navbar = () => {
  return (
    <div className='w-full  bg-white fixed top-0 inset-e-0 px-10 py-2 z-100'>
       <Logo className="w-40 h-auto"/> 
    </div>
  )
}

export default Navbar