"use client"
import Logo from "@/public/logo.svg";
import SearchInput from "./SearchInput";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ProcessDesc } from "@/functions/ProcessDesc";
import { useState , useEffect} from "react";
import { ProjectPrediction } from "@/interfaces/projectPrediction";
import Result from "../Result/Result";
import { ProjectSettingsState } from "@/interfaces/taxonomy";
import { SimilarProjects } from "@/functions/SimilarProjects";
import { SimilarProject } from "@/interfaces/similarProjects";

const SearchPage = () => {
    // const queryClient = useQueryClient();
    const [ready , setReady] = useState(false)
    const [result , setResult] = useState<ProjectPrediction>()
    const [similarProjects , setSimilarProjects] = useState<SimilarProject[]>()

const smoothScrollTo = (element: HTMLElement, duration = 1000) => {
  const start = window.scrollY;

  // 100px gap from the top of the screen
  const target =
    element.getBoundingClientRect().top + window.scrollY - 100;

  const distance = target - start;

  let startTime: number | null = null;

  const animate = (currentTime: number) => {
    if (startTime === null) startTime = currentTime;

    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);

    // Ease-in-out
    const eased =
      progress < 0.5
        ? 2 * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 2) / 2;

    window.scrollTo(0, start + distance * eased);

    if (progress < 1) {
      requestAnimationFrame(animate);
    }
  };

  requestAnimationFrame(animate);
};
useEffect(() => {
  if (!ready) return;

  const result = document.getElementById("result");

  if (result) {
    smoothScrollTo(result, 1200);
  }
}, [ready]);

  const similarProjectsMutate = useMutation({
      mutationFn: async ({
      settings
      }: {
      settings: ProjectSettingsState;
      }) =>SimilarProjects(settings , 10),
      onSuccess: (res) => {
      console.log("res", res);
      setSimilarProjects(res)

      // window.alert("hiii")
      },
      onError: (error) => {
      console.log(error);
      // window.alert("bye")
      // setIsLoading(false);
      },
  });
    
  const searchMutate = useMutation({
      mutationFn: async ({
      projectDesc
      }: {
      projectDesc: string;
      }) =>ProcessDesc(projectDesc),
      onSuccess: (res) => {
      console.log("res", res);
      setReady(true)
      setResult(res)
      if(res?.suggested_settings){
        similarProjectsMutate.mutate({settings:res?.suggested_settings})
      }

      // window.alert("hiii")
      },
      onError: (error) => {
      console.log(error);
      // window.alert("bye")
      // setIsLoading(false);
      },
  });
    return (
        <main className="">
            <div className="w-full mt-40 mb-20 h-full flex flex-col justify-center items-center">
                <Logo className="md:w-1/3 sm:w-1/2"/>
                <SearchInput mutation={searchMutate}/>
            </div>
            <Result similarProjects={similarProjects} ready={ready} result={result} />
        </main>
    )
}

export default SearchPage